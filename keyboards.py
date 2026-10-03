from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
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
            [KeyboardButton(text="⚙️ Категории"), KeyboardButton(text="↩️ Удалить последнюю")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
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
                InlineKeyboardButton(text="‹ Назад", callback_data="flow_back:category"),
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


def history_keyboard(active: str = "all") -> InlineKeyboardMarkup:
    labels = {"all": "Все", "expense": "Расходы", "income": "Доходы"}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("• " if key == active else "") + label,
                    callback_data=f"history:{key}",
                )
                for key, label in labels.items()
            ]
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
