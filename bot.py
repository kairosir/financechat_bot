from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.types import ReplyKeyboardRemove
from dotenv import load_dotenv

from database import Database, Transaction
from keyboards import (
    categories_keyboard,
    category_label,
    confirm_undo_keyboard,
    main_keyboard,
    note_keyboard,
    summary_keyboard,
)


router = Router()
db: Database
timezone: ZoneInfo


class AddTransaction(StatesGroup):
    choosing_category = State()
    entering_amount = State()
    entering_note = State()


def parse_amount(value: str) -> int | None:
    cleaned = value.strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999.99"):
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(amount_minor: int) -> str:
    value = Decimal(amount_minor) / 100
    if value == value.to_integral():
        return f"{int(value):,}".replace(",", " ") + " ₸"
    return f"{value:,.2f}".replace(",", " ") + " ₸"


def format_transaction(item: Transaction, include_date: bool = True) -> str:
    sign = "−" if item.kind == "expense" else "+"
    label = category_label(item.kind, item.category)
    date = datetime.fromisoformat(item.created_at).strftime("%d.%m · %H:%M")
    note = f" · {html.escape(item.note)}" if item.note else ""
    prefix = f"{date}  " if include_date else ""
    return f"{prefix}{label}{note}\n<b>{sign}{format_money(item.amount_minor)}</b>"


async def start_transaction(message: Message, state: FSMContext, kind: str) -> None:
    await state.clear()
    await state.set_state(AddTransaction.choosing_category)
    await state.update_data(kind=kind)
    title = "расхода" if kind == "expense" else "дохода"
    await message.answer("Добавляем операцию…", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        f"Выберите категорию {title}:",
        reply_markup=categories_keyboard(kind),
    )


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = html.escape(message.from_user.first_name) if message.from_user else ""
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Я сохраню доходы и расходы, а затем покажу понятную сводку. "
        "Дата и время записываются автоматически.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("help"))
async def command_help(message: Message) -> None:
    await message.answer(
        "<b>Как пользоваться</b>\n\n"
        "1. Нажмите «Расход» или «Доход».\n"
        "2. Выберите категорию.\n"
        "3. Введите сумму и, при желании, комментарий.\n\n"
        "Команда /cancel отменяет текущее действие.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("cancel"))
@router.message(F.text == "Отмена")
async def cancel_by_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())


@router.callback_query(F.data == "cancel")
async def cancel_by_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text("Действие отменено.")
        await callback.message.answer("Главное меню:", reply_markup=main_keyboard())


@router.message(F.text == "➖ Расход")
async def expense(message: Message, state: FSMContext) -> None:
    await start_transaction(message, state, "expense")


@router.message(F.text == "➕ Доход")
async def income(message: Message, state: FSMContext) -> None:
    await start_transaction(message, state, "income")


@router.callback_query(
    AddTransaction.choosing_category,
    F.data.startswith("category:"),
)
async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    _, callback_kind, category = callback.data.split(":", maxsplit=2)
    data = await state.get_data()
    if callback_kind != data.get("kind"):
        await callback.answer("Эта кнопка уже неактуальна", show_alert=True)
        return
    await state.update_data(category=category)
    await state.set_state(AddTransaction.entering_amount)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"{category_label(callback_kind, category)}\n\n"
            "Введите сумму, например: <b>2500</b> или <b>1250,50</b>"
        )


@router.message(AddTransaction.choosing_category)
async def category_hint(message: Message) -> None:
    await message.answer("Выберите категорию кнопкой выше или нажмите /cancel.")


@router.message(AddTransaction.entering_amount)
async def enter_amount(message: Message, state: FSMContext) -> None:
    amount_minor = parse_amount(message.text or "")
    if amount_minor is None:
        await message.answer("Не получилось распознать сумму. Введите число больше нуля:")
        return
    await state.update_data(amount_minor=amount_minor)
    await state.set_state(AddTransaction.entering_note)
    await message.answer(
        f"Сумма: <b>{format_money(amount_minor)}</b>\n\n"
        "Добавьте короткий комментарий или пропустите:",
        reply_markup=note_keyboard(),
    )


async def save_transaction(
    message: Message,
    state: FSMContext,
    note: str | None,
) -> None:
    data = await state.get_data()
    kind = str(data["kind"])
    category = str(data["category"])
    amount_minor = int(data["amount_minor"])
    now = datetime.now(timezone)
    await db.add_transaction(
        user_id=message.chat.id,
        kind=kind,
        category=category,
        amount_minor=amount_minor,
        note=note,
        created_at=now,
    )
    await state.clear()
    sign = "−" if kind == "expense" else "+"
    verb = "Расход сохранён" if kind == "expense" else "Доход сохранён"
    note_line = f"\nКомментарий: {html.escape(note)}" if note else ""
    await message.answer(
        f"✅ <b>{verb}</b>\n\n"
        f"{category_label(kind, category)}\n"
        f"<b>{sign}{format_money(amount_minor)}</b>{note_line}\n"
        f"{now.strftime('%d.%m.%Y · %H:%M')}",
        reply_markup=main_keyboard(),
    )


@router.message(AddTransaction.entering_note)
async def enter_note(message: Message, state: FSMContext) -> None:
    note = (message.text or "").strip()
    if not note:
        await message.answer("Введите комментарий или нажмите «Пропустить».")
        return
    if len(note) > 100:
        await message.answer("Сократите комментарий до 100 символов.")
        return
    await save_transaction(message, state, note)


@router.callback_query(AddTransaction.entering_note, F.data == "skip_note")
async def skip_note(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await save_transaction(callback.message, state, None)


@router.message(F.text == "🕘 История")
async def history(message: Message) -> None:
    items = await db.recent(message.chat.id, limit=10)
    if not items:
        await message.answer("История пока пуста. Добавьте первую операцию.")
        return
    text = "<b>Последние операции</b>\n\n" + "\n\n".join(
        format_transaction(item) for item in items
    )
    await message.answer(text)


def period_start(period: str) -> tuple[datetime | None, str]:
    now = datetime.now(timezone)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), "Сегодня"
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), "Этот месяц"
    return None, "Всё время"


async def send_summary(message: Message, period: str) -> None:
    start, title = period_start(period)
    income_total, expense_total, categories = await db.totals(message.chat.id, start)
    balance = income_total - expense_total
    sign = "+" if balance > 0 else ("−" if balance < 0 else "")
    balance_value = abs(balance)

    lines = [
        f"<b>{title}</b>",
        "",
        f"Доходы       <b>+{format_money(income_total)}</b>",
        f"Расходы      <b>−{format_money(expense_total)}</b>",
        f"Баланс        <b>{sign}{format_money(balance_value)}</b>",
    ]
    if categories:
        lines.extend(["", "<b>Основные расходы</b>"])
        lines.extend(
            f"{category_label('expense', category)} — {format_money(total)}"
            for category, total in categories
        )
    await message.answer("\n".join(lines), reply_markup=summary_keyboard())


@router.message(F.text == "📊 Сводка")
async def summary(message: Message) -> None:
    await send_summary(message, "month")


@router.callback_query(F.data.startswith("summary:"))
async def switch_summary(callback: CallbackQuery) -> None:
    period = callback.data.split(":", maxsplit=1)[1]
    await callback.answer()
    if callback.message:
        await callback.message.delete()
        await send_summary(callback.message, period)


@router.message(F.text == "↩️ Отменить последнюю")
async def ask_undo(message: Message) -> None:
    item = await db.last(message.chat.id)
    if item is None:
        await message.answer("Удалять пока нечего.")
        return
    await message.answer(
        "Удалить последнюю операцию?\n\n" + format_transaction(item),
        reply_markup=confirm_undo_keyboard(item.id),
    )


@router.callback_query(F.data.startswith("undo:"))
async def confirm_undo(callback: CallbackQuery) -> None:
    transaction_id = int(callback.data.split(":", maxsplit=1)[1])
    user_id = callback.message.chat.id if callback.message else callback.from_user.id
    deleted = await db.delete_owned(transaction_id, user_id)
    await callback.answer("Удалено" if deleted else "Операция уже удалена")
    if callback.message:
        await callback.message.edit_text(
            "↩️ Операция удалена." if deleted else "Эта операция уже удалена."
        )


@router.callback_query(F.data == "undo_cancel")
async def cancel_undo(callback: CallbackQuery) -> None:
    await callback.answer("Оставлено")
    if callback.message:
        await callback.message.edit_text("Операция сохранена без изменений.")


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer("Выберите действие кнопкой ниже 👇", reply_markup=main_keyboard())


async def main() -> None:
    global db, timezone

    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

    timezone_name = os.getenv("BOT_TIMEZONE", "Asia/Almaty")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"Неизвестный часовой пояс: {timezone_name}") from error

    db = Database(os.getenv("DATABASE_PATH", "data/finance.db"))
    await db.initialize()

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())
