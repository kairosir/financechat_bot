from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


EXPENSE_CATEGORIES = {
    "food": "🍽 Еда",
    "transport": "🚕 Транспорт",
    "home": "🏠 Дом",
    "shopping": "🛍 Покупки",
    "health": "💊 Здоровье",
    "fun": "🎉 Развлечения",
    "other": "📦 Другое",
}

INCOME_CATEGORIES = {
    "salary": "💼 Зарплата",
    "freelance": "💻 Подработка",
    "gift": "🎁 Подарок",
    "investment": "📈 Инвестиции",
    "other": "📦 Другое",
}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➖ Расход"), KeyboardButton(text="➕ Доход")],
            [KeyboardButton(text="📊 Сводка"), KeyboardButton(text="🕘 История")],
            [KeyboardButton(text="↩️ Отменить последнюю")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def categories_keyboard(kind: str) -> InlineKeyboardMarkup:
    categories = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    rows = []
    items = list(categories.items())
    for index in range(0, len(items), 2):
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"category:{kind}:{key}",
                )
                for key, label in items[index : index + 2]
            ]
        )
    rows.append([InlineKeyboardButton(text="✕ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_note")],
            [InlineKeyboardButton(text="✕ Отмена", callback_data="cancel")],
        ]
    )


def summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="summary:day"),
                InlineKeyboardButton(text="Месяц", callback_data="summary:month"),
                InlineKeyboardButton(text="Всё время", callback_data="summary:all"),
            ]
        ]
    )


def confirm_undo_keyboard(transaction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Удалить",
                    callback_data=f"undo:{transaction_id}",
                ),
                InlineKeyboardButton(text="Оставить", callback_data="undo_cancel"),
            ]
        ]
    )


def category_label(kind: str, key: str) -> str:
    categories = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    return categories.get(key, "📦 Другое")

