from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)


EXPENSE_CATEGORIES = {
    "food": "🍽 Еда",
    "transport": "🚕 Транспорт",
    "home": "🏠 Дом и счета",
    "shopping": "🛍 Покупки",
    "health": "💊 Здоровье",
    "fun": "🎉 Развлечения",
    "education": "📚 Образование",
    "other": "📦 Другое",
}

INCOME_CATEGORIES = {
    "salary": "💼 Зарплата",
    "freelance": "💻 Подработка",
    "gift": "🎁 Подарок",
    "investment": "📈 Инвестиции",
    "refund": "↩️ Возврат",
    "other": "📦 Другое",
}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➖ Расход"), KeyboardButton(text="➕ Доход")],
            [KeyboardButton(text="📊 Сводка"), KeyboardButton(text="🕘 История")],
            [KeyboardButton(text="💳 Счета"), KeyboardButton(text="⚙️ Категории")],
            [KeyboardButton(text="↩️ Удалить последнюю"), KeyboardButton(text="📱 iPhone")],
            [KeyboardButton(text="🌐 Веб-приложение")],
            [KeyboardButton(text="✨ Что нового?"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def iphone_setup_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Настроить команду iPhone", url=url)],
            [InlineKeyboardButton(text="✕ Закрыть", callback_data="iphone_close")],
        ]
    )


def web_app_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть приложение",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def telegram_login_confirm_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить и открыть",
                    web_app=WebAppInfo(url=url),
                )
            ],
            [InlineKeyboardButton(text="✕ Отмена", callback_data="web_login_cancel")],
        ]
    )




def categories_keyboard(
    kind: str,
    custom_categories: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    defaults = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"category:{kind}:base:{key}")
        for key, label in defaults.items()
    ]
    buttons.extend(
        InlineKeyboardButton(
            text=custom_label(name),
            callback_data=f"category:{kind}:user:{item_id}",
        )
        for item_id, name in custom_categories
    )
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.extend(
        [
            [InlineKeyboardButton(text="＋ Своя категория", callback_data=f"flow_add_category:{kind}")],
            [InlineKeyboardButton(text="✕ Отмена", callback_data="cancel")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_icon(account_type: str) -> str:
    return {"deposit": "🏦", "cash": "💵"}.get(account_type, "💳")


def account_keyboard(accounts: list[tuple[int, str, int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{account_icon(account_type)} {name}",
                callback_data=f"account_select:{account_id}",
            )
        ]
        for account_id, name, _, account_type in accounts
    ]
    rows.append(
        [
            InlineKeyboardButton(text="‹ Назад", callback_data="flow_back:category"),
            InlineKeyboardButton(text="✕ Отмена", callback_data="cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def amount_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="500 ₸", callback_data="amount:500"),
                InlineKeyboardButton(text="1 000 ₸", callback_data="amount:1000"),
            ],
            [
                InlineKeyboardButton(text="2 500 ₸", callback_data="amount:2500"),
                InlineKeyboardButton(text="5 000 ₸", callback_data="amount:5000"),
            ],
            [InlineKeyboardButton(text="⌨️ Ввести другую сумму", callback_data="amount:custom")],
            [
                InlineKeyboardButton(text="‹ Назад", callback_data="flow_back:account"),
                InlineKeyboardButton(text="✕ Отмена", callback_data="cancel"),
            ],
        ]
    )


def note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без комментария", callback_data="skip_note")],
            [
                InlineKeyboardButton(text="‹ Назад", callback_data="flow_back:amount"),
                InlineKeyboardButton(text="✕ Отмена", callback_data="cancel"),
            ],
        ]
    )


def back_cancel_keyboard(back_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="‹ Назад", callback_data=back_data),
                InlineKeyboardButton(text="✕ Отмена", callback_data="cancel"),
            ]
        ]
    )


def add_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➖ Расход", callback_data="add_again:expense"),
                InlineKeyboardButton(text="➕ Доход", callback_data="add_again:income"),
            ],
            [InlineKeyboardButton(text="✕ Закрыть", callback_data="cancel")],
        ]
    )


def after_save_keyboard(kind: str) -> InlineKeyboardMarkup:
    title = "Ещё расход" if kind == "expense" else "Ещё доход"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"＋ {title}", callback_data=f"add_again:{kind}")],
            [InlineKeyboardButton(text="📊 Открыть сводку", callback_data="summary:month")],
        ]
    )


def summary_keyboard(active: str = "month") -> InlineKeyboardMarkup:
    labels = {"day": "Сегодня", "month": "Месяц", "all": "Всё время"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("• " if key == active else "") + label,
                    callback_data=f"summary:{key}",
                )
                for key, label in labels.items()
            ],
            [InlineKeyboardButton(text="＋ Добавить операцию", callback_data="summary:add")],
        ]
    )


def history_keyboard(active: str = "month") -> InlineKeyboardMarkup:
    labels = {
        "day": "День",
        "week": "Неделя",
        "month": "Месяц",
        "year": "Год",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("• " if key == active else "") + label,
                    callback_data=f"history_period:{key}",
                )
                for key, label in labels.items()
            ][:2],
            [
                InlineKeyboardButton(
                    text=("• " if key == active else "") + label,
                    callback_data=f"history_period:{key}",
                )
                for key, label in list(labels.items())[2:]
            ],
            [
                InlineKeyboardButton(text="Выбрать даты", callback_data="history_period:custom"),
                InlineKeyboardButton(text="Всё время", callback_data="history_period:all"),
            ],
        ]
    )


def account_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Карта", callback_data="account_type:card"),
                InlineKeyboardButton(text="🏦 Депозит", callback_data="account_type:deposit"),
            ],
            [InlineKeyboardButton(text="💵 Наличные", callback_data="account_type:cash")],
            [InlineKeyboardButton(text="‹ К счетам", callback_data="accounts:back")],
        ]
    )


def currency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="₸ Тенге", callback_data="account_currency:KZT"),
                InlineKeyboardButton(text="₽ Рубль", callback_data="account_currency:RUB"),
                InlineKeyboardButton(text="$ Доллар", callback_data="account_currency:USD"),
            ],
            [InlineKeyboardButton(text="‹ К счетам", callback_data="accounts:back")],
        ]
    )


def deposit_daily_interest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✓ Да, ежедневно",
                    callback_data="deposit_daily_interest:yes",
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data="deposit_daily_interest:no",
                ),
            ],
            [InlineKeyboardButton(text="‹ К счетам", callback_data="accounts:back")],
        ]
    )


def manage_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➖ Расходы", callback_data="manage:expense"),
                InlineKeyboardButton(text="➕ Доходы", callback_data="manage:income"),
            ],
            [InlineKeyboardButton(text="✕ Закрыть", callback_data="manage:close")],
        ]
    )


def accounts_manage_keyboard(accounts: list[tuple[int, str, int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{account_icon(account_type)} {name}",
                callback_data=f"account_manage:{account_id}",
            )
        ]
        for account_id, name, _, account_type in accounts
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="＋ Добавить счёт", callback_data="accounts:add")],
            [InlineKeyboardButton(text="⇄ Перевод средств", callback_data="accounts:transfer")],
            [InlineKeyboardButton(text="✕ Закрыть", callback_data="accounts:close")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_manage_keyboard(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Переименовать",
                    callback_data=f"account_rename:{account_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Установить остаток",
                    callback_data=f"account_balance:{account_id}",
                )
            ],
            [InlineKeyboardButton(text="‹ Назад", callback_data="accounts:back")],
        ]
    )


def transfer_account_keyboard(
    accounts: list[tuple[int, str, int, str]],
    direction: str,
    excluded_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{account_icon(account_type)} {name}",
                callback_data=f"transfer_{direction}:{account_id}",
            )
        ]
        for account_id, name, _, account_type in accounts
        if account_id != excluded_id
    ]
    rows.append([InlineKeyboardButton(text="‹ К счетам", callback_data="accounts:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_users_keyboard(users: list[dict[str, object]]) -> InlineKeyboardMarkup:
    rows = []
    for item in users[:40]:
        user_id = int(item["user_id"])
        name = str(item.get("display_name") or item.get("username") or f"ID {user_id}")
        rows.append(
            [InlineKeyboardButton(text=f"👤 {name}", callback_data=f"admin_user:{user_id}")]
        )
    rows.append([InlineKeyboardButton(text="✕ Закрыть", callback_data="admin_close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="‹ К пользователям", callback_data="admin_back")],
            [InlineKeyboardButton(text="✕ Закрыть", callback_data="admin_close")],
        ]
    )


def manage_categories_keyboard(
    kind: str,
    custom_categories: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🗑 {custom_label(name)}",
                callback_data=f"category_delete:{item_id}",
            )
        ]
        for item_id, name in custom_categories
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="＋ Добавить категорию", callback_data=f"manage_add:{kind}")],
            [InlineKeyboardButton(text="‹ Назад", callback_data="manage:back")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_category_delete_keyboard(item_id: int, kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Удалить",
                    callback_data=f"category_delete_confirm:{item_id}",
                ),
                InlineKeyboardButton(text="Оставить", callback_data=f"manage:{kind}"),
            ]
        ]
    )


def confirm_undo_keyboard(transaction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Удалить", callback_data=f"undo:{transaction_id}"),
                InlineKeyboardButton(text="Оставить", callback_data="undo_cancel"),
            ]
        ]
    )


def category_label(kind: str, key: str) -> str:
    if key.startswith("custom|"):
        return custom_label(key.split("|", maxsplit=1)[1])
    categories = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    return categories.get(key, "📦 Другое")


def custom_label(name: str) -> str:
    return name if name and not name[0].isalnum() else f"🏷 {name}"
