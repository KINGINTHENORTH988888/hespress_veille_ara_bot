import html
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

URL = "https://www.hespress.com/"
HISTORY_FILE = "historique_hespress_ar.txt"
DIGEST_FILE = "articles_du_jour_ar.json"
MAX_ALERTS_PER_RUN = 1

TELEGRAM_TOKEN = (os.environ.get("HESPRESS_AR_TELEGRAM_TOKEN") or "").strip()
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
        "الأمن الوطني",
        "المديرية العامة للأمن الوطني",
        "الشرطة",
        "عميد الشرطة",
        "الأمن العمومي",
        "ولاية الأمن",
        "الدرك الملكي",
        "توقيف",
        "اعتقال",
    ],
    "BUDGET": [
        "قانون المالية",
        "الميزانية",
        "ميزانية",
        "لقجع",
        "الضريبة",
        "الضرائب",
        "الخزينة",
        "الدعم الاجتماعي",
        "المديونية",
        "الدين العمومي",
    ],
    "INTERIEUR": [
        "وزارة الداخلية",
        "الداخلية",
        "والي",
        "ولاة",
        "عامل إقليم",
        "عمال الأقاليم",
        "ولاة وعمال",
        "قائد",
        "قياد",
        "الإدارة الترابية",
        "الجماعات الترابية",
        "الانتخابات",
    ],
    "SAHARA": [
        "الصحراء المغربية",
        "الأقاليم الجنوبية",
        "الصحراء",
        "العيون",
        "الداخلة",
        "الحكم الذاتي",
        "الأمم المتحدة",
        "مينورسو",
        "قنصلية",
    ],
    "ECONOMIE": [
        "الاقتصاد",
        "اقتصادي",
        "الاستثمار",
        "استثمارات",
        "النمو",
        "الشغل",
        "التشغيل",
        "البطالة",
        "التصدير",
        "الصادرات",
        "الصناعة",
        "السياحة",
        "البنك",
        "الأبناك",
        "التضخم",
        "مقاولة",
        "مقاولات",
        "التجارة",
        "العقار",
        "الفندقة",
    ],
}

EXCLUDED_PATHS = (
    "/category/", "/tag/", "/author/", "/videos/", "/medias/",
    "/contact", "/a-propos", "/mentions-legales", "/confidentialite",
    "/tous-les-articles", "/permalink/"
)


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


def send_telegram(text: str) -> tuple[bool, bool]:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Secrets Telegram manquants.")
        return False, True
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(api_url, json=payload, timeout=15)
        if r.status_code == 429:
            print(f"Erreur Telegram (429) — arrêt immédiat du run : {r.text}")
            return False, True
        if not r.ok:
            print(f"Erreur Telegram ({r.status_code}) : {r.text}")
            return False, False
        return True, False
    except Exception as e:
        print(f"Exception Telegram — arrêt immédiat du run : {e}")
        return False, True


def match_keyword(haystack_norm: str) -> tuple[str | None, str | None]:
    word_list = re.findall(r"[\u0621-\u064A]+", haystack_norm)
    tokens = set(word_list)
    padded = f" {' '.join(word_list)} "

    for axe, words in KEYWORDS.items():
        for w in words:
            w_norm = normalize_ar(w)
            if not w_norm:
                continue
            w_parts = re.findall(r"[\u0621-\u064A]+", w_norm)
            if not w_parts:
                continue
            if len(w_parts) == 1:
                if w_parts[0] in tokens:
                    return axe, w
            else:
                phrase = " ".join(w_parts)
                if f" {phrase} " in padded:
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


def _is_valid_summary_text(txt: str, title: str) -> bool:
    if not txt or len(txt) < 25:
        return False
    if txt == title or title in txt or txt in title:
        return False
    return True


def extract_card_summary(anchor, title: str) -> str:
    curr = anchor
    for _ in range(3):
        parent = curr.find_parent(["article", "div", "li"])
        if not parent:
            break

        candidates = []
        for tag in parent.find_all(["p", "div", "span"], recursive=True):
            if tag == anchor or anchor in tag.parents:
                continue
            if tag.find("a") is not None:
                continue
            cls_str = " ".join(tag.get("class") or []).lower()
            is_excerpt_like = (
                tag.name == "p"
                or any(k in cls_str for k in ("excerpt", "summary", "chapo", "lead", "desc", "subtitle"))
            )
            if is_excerpt_like:
                txt = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
                if _is_valid_summary_text(txt, title):
                    candidates.append((0 if tag.name == "p" else 1, txt))

        if candidates:
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]

        for tag in parent.find_all(["p", "span", "div"], recursive=True):
            if tag == anchor or anchor in tag.parents or tag.find("a") is not None:
                continue
            txt = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
            if _is_valid_summary_text(txt, title):
                return txt

        curr = parent
    return ""


def fetch_article_summary_from_page(article_url: str, title: str) -> str:
    try:
        resp = requests.get(article_url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for attrs in ({"name": "description"}, {"property": "og:description"}):
            meta = soup.find("meta", attrs=attrs)
            if meta and meta.get("content"):
                desc = re.sub(r"\s+", " ", meta["content"]).strip()
                if len(desc) >= 20 and desc != title and title not in desc:
                    return desc

        for p in soup.select("article p, .post-content p, .article-content p, main p"):
            txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
            if len(txt) >= 35 and txt != title:
                return txt
    except Exception:
        pass
    return ""


def fetch_hespress_articles() -> list[dict]:
    resp = requests.get(URL, headers=HEADERS, timeout=15)
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

        summary = extract_card_summary(a, title)

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
    except Exception as e:
        print(f"Erreur scraping : {e}")
        return

    new_urls = []
    alerts_sent = 0
    now_str = morocco_now().strftime("%H:%M")

    for art in articles:
        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break

        url = art["url"]
        if url in history:
            continue

        combined_norm = normalize_ar(f"{art['titre']} {art['resume']}")
        axe, kw = match_keyword(combined_norm)
        if not axe:
            continue

        raw_resume = art["resume"]
        if not raw_resume:
            raw_resume = fetch_article_summary_from_page(url, art["titre"])

        titre_clean = art["titre"][:220]
        resume_clean = (
            (raw_resume[:320] + "...")
            if len(raw_resume) > 320
            else (raw_resume or "الملخص غير متوفر حاليا، يرجى فتح الرابط لقراءة المقال.")
        )

        msg = (
            f"<b>العنوان :</b> {html.escape(titre_clean)}\n"
            f"<b>الرابط :</b> <a href=\"{html.escape(url)}\">{html.escape(url)}</a>\n"
            f"<b>المحور :</b> {html.escape(axe)}\n"
            f"<b>الكلمة المفتاحية :</b> {html.escape(kw)}\n"
            f"<b>ملخص الخبر :</b> {html.escape(resume_clean)}"
        )

        ok, stop_run = send_telegram(msg)
        if stop_run:
            break
        if ok:
            alerts_sent += 1
            new_urls.append(url)
            buffer.append({
                "heure": now_str,
                "axe": axe,
                "mot_cle": kw,
                "titre": titre_clean,
                "resume": resume_clean,
                "url": url,
            })

    if new_urls:
        save_history(new_urls)
        save_digest_buffer(buffer)

    print(f"Terminé : {alerts_sent} nouvelle(s) alerte(s) envoyée(s).")


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
    ok, _ = send_telegram(msg)
    if ok:
        save_digest_buffer([])


if __name__ == "__main__":
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "realtime"
    if mode == "digest":
        run_digest()
    else:
        run_realtime()
