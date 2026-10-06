from __future__ import annotations

import html


RELEASES = [
    {
        "version": "1.2",
        "date": "6 октября 2026",
        "title": "Счета, валюты и гибкая история",
        "items": [
            "История за день, неделю, месяц, год или выбранные даты",
            "Счета типов «Карта» и «Депозит»",
            "Годовая ставка для депозитов",
            "Тенге, рубли и доллары без смешивания сумм",
            "Исправлено закрытие окна создания счёта в Mini App",
        ],
    },
    {
        "version": "1.1",
        "date": "5 октября 2026",
        "title": "Purpose Finance теперь в Vercel",
        "items": [
            "Telegram-бот и Mini App работают с одной базой Neon",
            "Добавлены счета, собственные категории и комментарии",
            "Появилась быстрая запись через Команды на iPhone",
        ],
    },
]


def releases_payload() -> dict[str, object]:
    return {"ok": True, "releases": RELEASES}


def releases_html() -> str:
    blocks: list[str] = ["<b>✨ Что нового?</b>"]
    for release in RELEASES:
        blocks.extend(
            [
                "",
                f"<b>Версия {html.escape(str(release['version']))}</b> · {html.escape(str(release['date']))}",
                f"<i>{html.escape(str(release['title']))}</i>",
            ]
        )
        blocks.extend(f"• {html.escape(str(item))}" for item in release["items"])
    return "\n".join(blocks)
