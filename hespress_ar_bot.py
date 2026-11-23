import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

URL = "https://www.hespress.com/"
HISTORY_FILE = "historique_hespress_ar.txt"
DIGEST_FILE = "articles_du_jour_ar.json"
MAX_ALERTS_PER_RUN = 1

TELEGRAM_TOKEN = os.environ.get("HESPRESS_AR_TELEGRAM_TOKEN")
CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,fr-FR;q=0.9,en-US;q=0.8",
}

KEYWORDS = {
    "DGSN": [
        "الأمن الوطني", "المديرية العامة للأمن الوطني", "الشرطة", "عميد الشرطة",
        "فرقة", "الأمن العمومي", "ولاية الأمن", "توقيف", "اعتقال", "الدرك الملكي"
    ],
    "BUDGET": [
        "قانون المالية", "الميزانية", "ميزانية", "لقجع",
        "الضريبة", "الضرائب", "الخزينة", "الدعم الاجتماعي", "المديونية", "الدين العمومي"
    ],
    "INTERIEUR": [
        "وزارة الداخلية", "الداخلية", "والي", "ولاة",
        "عامل", "عمال", "قائد", "قياد",
        "الإدارة الترابية", "الجماعات الترابية", "الجهة", "الجماعة", "الانتخابات"
    ],
    "SAHARA": [
        "الصحراء", "الصحراء المغربية", "الأقاليم الجنوبية", "العيون",
        "الداخلة", "الحكم الذاتي", "الأمم المتحدة", "مينورسو", "قنصلية"
    ],
    "ECONOMIE": [
        "الاقتصاد", "اقتصادي", "الاستثمار", "استثمارات",
        "النمو", "الشغل", "التشغيل", "البطالة", "التصدير", "الصادرات",
        "الصناعة", "السياحة", "البنك", "الأبناك", "التضخم",
        "مقاولة", "مقاولات", "التجارة", "العقار", "الفندقة"
    ],
}

EXCLUDED_PATHS = (
    "/category/", "/tag/", "/author/", "/videos/", "/medias/",
    "/contact", "/a-propos", "/mentions-legales", "/confidentialite",
    "/tous-les-articles", "/permalink/"
)


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_ar(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\u064B-\u065F\u0670]", "", text)
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(raw_title: str) -> str:
    t = re.sub(r"\s+", " ", raw_title or "").strip()
    t = re.sub(r"^\d{1,2}:\d{2}\s+", "", t).strip()
    return t


def morocco_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=1)))


def load_history() -> set[str]:
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_history(urls: list[str]) -> None:
    if not urls:
        return
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        for u in urls:
            f.write(u + "\n")


def load_digest_buffer() -> list[dict]:
    if not os.path.exists(DIGEST_FILE):
        return []
    try:
        with open(DIGEST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_digest_buffer(items: list[dict]) -> None:
    with open(DIGEST_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def send_telegram(text: str, max_retries: int = 2) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("Secrets Telegram manquants.")
        return False
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(api_url, json=payload, timeout=20)
            if r.ok:
                time.sleep(1.5)
                return True

            if r.status_code == 429:
                log(f"Limite Telegram (429), attente 6s (essai {attempt}/{max_retries})...")
                time.sleep(6)
                continue

            log(f"Erreur Telegram ({r.status_code}) : {r.text}")
            return False
        except Exception as e:
            log(f"Exception Telegram (essai {attempt}/{max_retries}) : {e}")
            time.sleep(6)

    return False


def match_keyword(haystack_norm: str) -> tuple[str | None, str | None]:
    padded = f" {haystack_norm} "
    for axe, words in KEYWORDS.items():
        for w in words:
            w_norm = normalize_ar(w)
            if not w_norm:
                continue
            if f" {w_norm} " in padded or w_norm in haystack_norm:
                return axe, w
    return None, None


def looks_like_article_url(clean_href: str) -> bool:
    if clean_href in ("https://www.hespress.com", "https://hespress.com"):
        return False
    if any(p in clean_href for p in EXCLUDED_PATHS):
        return False
    path = re.sub(r"^https?://(www\.)?hespress\.com", "", clean_href).strip("/")
    if not path:
        return False
    slug = path.split("/")[-1]
    return len(slug) >= 10 and ("-" in slug or re.search(r"\d+", slug) is not None)


def fetch_article_summary(article_url: str, fallback: str = "") -> str:
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=8)
        if not r.ok:
            return fallback
        soup = BeautifulSoup(r.text, "html.parser")

        container = (
            soup.select_one(".article-content")
            or soup.select_one(".entry-content")
            or soup.select_one("article")
            or soup
        )
        paragraphs = []
        for p in container.find_all("p"):
            txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
            if len(txt) >= 40 and not txt.startswith(("اقرأ أيضا", "اقرأ أيضاً", "شارك")):
                paragraphs.append(txt)
            if len(" ".join(paragraphs)) >= 340:
                break

        if paragraphs:
            joined = " ".join(paragraphs)
            return (joined[:340] + "...") if len(joined) > 340 else joined

        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            desc = re.sub(r"\s+", " ", meta_desc["content"]).strip()
            return (desc[:340] + "...") if len(desc) > 340 else desc
    except Exception:
        pass
    return fallback


def fetch_hespress_articles() -> list[dict]:
    resp = requests.get(URL, headers=HEADERS, timeout=12)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    seen_urls = set()

    for a in soup.select("a[href]"):
        raw_href = (a.get("href") or "").strip()
        if not raw_href or raw_href.startswith(("#", "mailto:", "javascript:")):
            continue

        href = urljoin(URL, raw_href)
        if not re.match(r"^https?://(www\.)?hespress\.com/", href):
            continue

        clean_href = href.split("?")[0].rstrip("/")
        if clean_href in seen_urls or not looks_like_article_url(clean_href):
            continue

        title = clean_title(a.get_text(" ", strip=True))
        if len(title) < 12:
            continue

        parent = a.find_parent(["article", "div", "li"])
        summary = ""
        if parent:
            p = parent.find("p")
            if p:
                summary = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
                if summary == title:
                    summary = ""

        seen_urls.add(clean_href)
        articles.append({
            "titre": title,
            "resume": summary,
            "url": clean_href,
        })

    return articles


def run_realtime() -> None:
    history = load_history()
    buffer = load_digest_buffer()

    try:
        articles = fetch_hespress_articles()
        log(f"{len(articles)} articles candidats trouvés sur Hespress AR.")
    except Exception as e:
        log(f"Erreur scraping : {e}")
        return

    new_urls = []
    alerts_sent = 0
    now_str = morocco_now().strftime("%H:%M")

    for art in articles:
        if alerts_sent >= MAX_ALERTS_PER_RUN:
            log(f"Plafond de {MAX_ALERTS_PER_RUN} alertes atteint pour ce run.")
            break

        url = art["url"]
        if url in history:
            continue

        combined_norm = normalize_ar(f"{art['titre']} {art['resume']}")
        axe, kw = match_keyword(combined_norm)
        if not axe:
            continue

        titre_clean = art["titre"][:220]
        full_summary = fetch_article_summary(url, fallback=art["resume"])
        if not full_summary:
            full_summary = "الملخص غير متوفر حاليا، يرجى فتح الرابط لقراءة المقال."

        msg = (
            f"<b>العنوان :</b> {html.escape(titre_clean)}\n"
            f"<b>الرابط :</b> <a href=\"{html.escape(url)}\">{html.escape(url)}</a>\n"
            f"<b>المحور :</b> {html.escape(axe)}\n"
            f"<b>الكلمة المفتاحية :</b> {html.escape(kw)}\n"
            f"<b>ملخص الخبر :</b> {html.escape(full_summary)}"
        )

        if send_telegram(msg):
            alerts_sent += 1
            new_urls.append(url)
            buffer.append({
                "heure": now_str,
                "axe": axe,
                "mot_cle": kw,
                "titre": titre_clean,
                "resume": full_summary,
                "url": url,
            })
            log(f"Alerte envoyée ({alerts_sent}/{MAX_ALERTS_PER_RUN}) [{axe} / {kw}]")

    if new_urls:
        save_history(new_urls)
        save_digest_buffer(buffer)

    log(f"Terminé : {alerts_sent} nouvelle(s) alerte(s) envoyée(s).")


def build_written_digest(items: list[dict], date_str: str) -> str:
    if not items:
        return f"<b>ملخص هسبريس — {date_str}</b>\n\nلم يتم رصد أي مقال خلال هذه الفترة."

    par_axe: dict[str, list[dict]] = {}
    for it in items:
        axe = it.get("axe", "أخرى")
        par_axe.setdefault(axe, []).append(it)

    parties = [
        f"<b>ملخص هسبريس — {date_str}</b>\n",
        (
            f"خلال هذه الفترة، تم رصد {len(items)} مقال(ات) مرتبطة بالأمن، "
            f"والميزانية، والإدارة الترابية، والصحراء، والاقتصاد:\n"
        ),
    ]

    for axe, articles in par_axe.items():
        parties.append(f"<b>• محور {html.escape(axe)} ({len(articles)}) :</b>")
        for art in articles:
            heure = art.get("heure", "")
            titre = html.escape(art.get("titre", "").strip())
            resume = html.escape(art.get("resume", "").strip())
            url = art.get("url", "").strip()

            bloc = f"— <b>{titre}</b>"
            if heure:
                bloc += f" <i>({html.escape(heure)})</i>"
            bloc += "\n"
            if resume:
                bloc += f"{resume}\n"
            if url:
                bloc += f"<a href=\"{html.escape(url)}\">{html.escape(url)}</a>"
            parties.append(bloc)

    return "\n\n".join(parties)


def run_digest() -> None:
    now = morocco_now()
    date_str = now.strftime("%d/%m/%Y")
    buffer = load_digest_buffer()

    if not buffer:
        send_telegram(f"<b>ملخص هسبريس — {date_str}</b>\n\nلم يتم رصد أي مقال خلال هذه الفترة.")
        return

    msg = build_written_digest(buffer, date_str)
    if send_telegram(msg):
        save_digest_buffer([])


if __name__ == "__main__":
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "realtime"
    if mode == "digest":
        run_digest()
    else:
        run_realtime()
