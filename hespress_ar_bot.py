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
