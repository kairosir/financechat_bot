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
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from dotenv import load_dotenv

from database import Database, Transaction
from keyboards import (
    add_kind_keyboard,
    amount_keyboard,
    back_cancel_keyboard,
    categories_keyboard,
    category_label,
    confirm_category_delete_keyboard,
    confirm_undo_keyboard,
    custom_label,
    history_keyboard,
    main_keyboard,
    manage_categories_keyboard,
    manage_kind_keyboard,
    note_keyboard,
    summary_keyboard,
)


router = Router()
db: Database
timezone: ZoneInfo


class AddTransaction(StatesGroup):
    choosing_category = State()
    entering_category = State()
    entering_amount = State()
    entering_note = State()


class ManageCategory(StatesGroup):
    entering_name = State()


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


def format_transaction(item: Transaction) -> str:
    sign = "−" if item.kind == "expense" else "+"
    label = html.escape(category_label(item.kind, item.category))
    date = datetime.fromisoformat(item.created_at).strftime("%d.%m · %H:%M")
    note = f"\n<i>{html.escape(item.note)}</i>" if item.note else ""
    return f"{date}  {label}\n<b>{sign}{format_money(item.amount_minor)}</b>{note}"


def normalize_category_name(value: str) -> str | None:
    name = " ".join(value.strip().split())
    if not 2 <= len(name) <= 30 or "|" in name:
        return None
    return name


async def get_custom_pairs(user_id: int, kind: str) -> list[tuple[int, str]]:
    items = await db.custom_categories(user_id, kind)
    return [(item.id, item.name) for item in items]


async def start_transaction(message: Message, state: FSMContext, kind: str) -> None:
    await state.clear()
    await state.set_state(AddTransaction.choosing_category)
    await state.update_data(kind=kind)
    title = "расхода" if kind == "expense" else "дохода"
    custom = await get_custom_pairs(message.chat.id, kind)
    await message.answer("Добавляем операцию…", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        f"<b>1 из 3 · Категория {title}</b>\n\nКуда отнести операцию?",
        reply_markup=categories_keyboard(kind, custom),
    )


async def edit_category_step(message: Message, state: FSMContext, user_id: int) -> None:
    data = await state.get_data()
    kind = str(data["kind"])
    title = "расхода" if kind == "expense" else "дохода"
    custom = await get_custom_pairs(user_id, kind)
    await state.set_state(AddTransaction.choosing_category)
    await message.edit_text(
        f"<b>1 из 3 · Категория {title}</b>\n\nКуда отнести операцию?",
        reply_markup=categories_keyboard(kind, custom),
    )


async def edit_amount_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = str(data["kind"])
    category = str(data["category"])
    await state.set_state(AddTransaction.entering_amount)
    await message.edit_text(
        "<b>2 из 3 · Сумма</b>\n\n"
        f"{html.escape(category_label(kind, category))}\n"
        "Выберите или введите сумму:",
        reply_markup=amount_keyboard(),
    )


async def edit_note_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AddTransaction.entering_note)
    await message.edit_text(
        "<b>3 из 3 · Комментарий</b>\n\n"
        f"Сумма: <b>{format_money(int(data['amount_minor']))}</b>\n"
        "Напишите комментарий или сохраните без него:",
        reply_markup=note_keyboard(),
    )


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = html.escape(message.from_user.first_name) if message.from_user else ""
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Здесь можно быстро записывать доходы и расходы, создавать свои категории "
        "и смотреть понятную статистику. Дата и время сохраняются автоматически.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def command_help(message: Message) -> None:
    await message.answer(
        "<b>Как пользоваться</b>\n\n"
        "➖ <b>Расход</b> и ➕ <b>Доход</b> — новая операция.\n"
        "📊 <b>Сводка</b> — баланс и основные траты.\n"
        "🕘 <b>История</b> — последние операции с фильтром.\n"
        "⚙️ <b>Категории</b> — свои направления доходов и расходов.\n"
        "↩️ <b>Удалить последнюю</b> — исправить ошибочную запись.\n\n"
        "Во время добавления используйте кнопки «Назад» и «Отмена». Комментарий необязателен.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("cancel"))
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


@router.callback_query(F.data.startswith("add_again:"))
async def add_again(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":", maxsplit=1)[1]
    await callback.answer()
    if callback.message:
        await start_transaction(callback.message, state, kind)


@router.callback_query(AddTransaction.choosing_category, F.data.startswith("category:"))
async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    _, callback_kind, source, value = callback.data.split(":", maxsplit=3)
    data = await state.get_data()
    if callback_kind != data.get("kind"):
        await callback.answer("Эта кнопка уже неактуальна", show_alert=True)
        return

    if source == "user":
        item = await db.get_custom_category(int(value), callback.from_user.id)
        if item is None or item.kind != callback_kind:
            await callback.answer("Категория уже удалена", show_alert=True)
            return
        category = f"custom|{item.name}"
    else:
        category = value

    await state.update_data(category=category)
    await callback.answer()
    if callback.message:
        await edit_amount_step(callback.message, state)


@router.callback_query(AddTransaction.choosing_category, F.data.startswith("flow_add_category:"))
async def flow_add_category(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":", maxsplit=1)[1]
    await state.set_state(AddTransaction.entering_category)
    await state.update_data(kind=kind)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Новая категория</b>\n\n"
            "Напишите название, например: <b>☕ Кофе</b> или <b>Питомец</b>.",
            reply_markup=back_cancel_keyboard("flow_back:category"),
        )


@router.message(AddTransaction.entering_category)
async def save_flow_category(message: Message, state: FSMContext) -> None:
    name = normalize_category_name(message.text or "")
    if name is None:
        await message.answer("Название должно содержать от 2 до 30 символов.")
        return
    data = await state.get_data()
    kind = str(data["kind"])
    current = await db.custom_categories(message.chat.id, kind)
    if len(current) >= 12:
        await message.answer("Для этого раздела уже создано 12 категорий. Удалите ненужную в настройках.")
        return
    item = await db.add_custom_category(message.chat.id, kind, name, datetime.now(timezone))
    if item is None:
        await message.answer("Такая категория уже существует. Введите другое название.")
        return
    await state.update_data(category=f"custom|{item.name}")
    await state.set_state(AddTransaction.entering_amount)
    await message.answer(
        "<b>2 из 3 · Сумма</b>\n\n"
        f"{html.escape(custom_label(item.name))}\n"
        "Выберите или введите сумму:",
        reply_markup=amount_keyboard(),
    )


@router.callback_query(F.data == "flow_back:category")
async def back_to_category(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "kind" not in data:
        await callback.answer("Действие уже завершено")
        return
    await callback.answer()
    if callback.message:
        await edit_category_step(callback.message, state, callback.from_user.id)


@router.callback_query(AddTransaction.entering_amount, F.data.startswith("amount:"))
async def choose_amount(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", maxsplit=1)[1]
    await callback.answer()
    if not callback.message:
        return
    if value == "custom":
        await callback.message.edit_text(
            "<b>2 из 3 · Сумма</b>\n\nВведите сумму сообщением, например: <b>1250,50</b>",
            reply_markup=back_cancel_keyboard("flow_back:category"),
        )
        return
    await state.update_data(amount_minor=int(value) * 100)
    await edit_note_step(callback.message, state)


@router.message(AddTransaction.entering_amount)
async def enter_amount(message: Message, state: FSMContext) -> None:
    amount_minor = parse_amount(message.text or "")
    if amount_minor is None:
        await message.answer("Не получилось распознать сумму. Введите число больше нуля:")
        return
    await state.update_data(amount_minor=amount_minor)
    await state.set_state(AddTransaction.entering_note)
    await message.answer(
        "<b>3 из 3 · Комментарий</b>\n\n"
        f"Сумма: <b>{format_money(amount_minor)}</b>\n"
        "Напишите комментарий или сохраните без него:",
        reply_markup=note_keyboard(),
    )


@router.callback_query(F.data == "flow_back:amount")
async def back_to_amount(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "category" not in data:
        await callback.answer("Действие уже завершено")
        return
    await callback.answer()
    if callback.message:
        await edit_amount_step(callback.message, state)


async def save_transaction(
    message: Message,
    state: FSMContext,
    note: str | None,
    user_id: int,
) -> None:
    data = await state.get_data()
    kind = str(data["kind"])
    category = str(data["category"])
    amount_minor = int(data["amount_minor"])
    now = datetime.now(timezone)
    await db.add_transaction(user_id, kind, category, amount_minor, note, now)
    await state.clear()
    sign = "−" if kind == "expense" else "+"
    verb = "Расход сохранён" if kind == "expense" else "Доход сохранён"
    note_line = f"\nКомментарий: {html.escape(note)}" if note else ""
    await message.answer(
        f"✅ <b>{verb}</b>\n\n"
        f"{html.escape(category_label(kind, category))}\n"
        f"<b>{sign}{format_money(amount_minor)}</b>{note_line}\n"
        f"{now.strftime('%d.%m.%Y · %H:%M')}",
        reply_markup=main_keyboard(),
    )


@router.message(AddTransaction.entering_note)
async def enter_note(message: Message, state: FSMContext) -> None:
    note = " ".join((message.text or "").strip().split())
    if not note:
        await message.answer("Введите комментарий или нажмите «Без комментария».")
        return
    if len(note) > 120:
        await message.answer("Сократите комментарий до 120 символов.")
        return
    await save_transaction(message, state, note, message.chat.id)


@router.callback_query(AddTransaction.entering_note, F.data == "skip_note")
async def skip_note(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await save_transaction(callback.message, state, None, callback.from_user.id)


async def send_history(message: Message, kind: str = "all", edit: bool = False) -> None:
    filter_kind = kind if kind in {"expense", "income"} else None
    items = await db.recent(message.chat.id, limit=10, kind=filter_kind)
    titles = {"all": "Последние операции", "expense": "Последние расходы", "income": "Последние доходы"}
    if items:
        text = f"<b>{titles[kind]}</b>\n\n" + "\n\n".join(format_transaction(item) for item in items)
    else:
        text = f"<b>{titles[kind]}</b>\n\nЗдесь пока нет операций."
    markup = history_keyboard(kind)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == "🕘 История")
async def history(message: Message) -> None:
    await send_history(message)


@router.callback_query(F.data.startswith("history:"))
async def switch_history(callback: CallbackQuery) -> None:
    kind = callback.data.split(":", maxsplit=1)[1]
    await callback.answer()
    if callback.message:
        await send_history(callback.message, kind, edit=True)


def period_start(period: str) -> tuple[datetime | None, str]:
    now = datetime.now(timezone)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), "Сегодня"
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), "Этот месяц"
    return None, "Всё время"


async def send_summary(message: Message, period: str, edit: bool = False) -> None:
    start, title = period_start(period)
    income_total, expense_total, categories = await db.totals(message.chat.id, start)
    balance = income_total - expense_total
    sign = "+" if balance > 0 else ("−" if balance < 0 else "")
    lines = [
        f"<b>📊 {title}</b>",
        "",
        f"Доходы      <b>+{format_money(income_total)}</b>",
        f"Расходы     <b>−{format_money(expense_total)}</b>",
        f"Баланс       <b>{sign}{format_money(abs(balance))}</b>",
    ]
    if categories:
        lines.extend(["", "<b>Основные расходы</b>"])
        for category, total in categories:
            percent = round(total / expense_total * 100) if expense_total else 0
            label = html.escape(category_label("expense", category))
            lines.append(f"{label} — {format_money(total)} · {percent}%")
    text = "\n".join(lines)
    markup = summary_keyboard(period)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == "📊 Сводка")
async def summary(message: Message) -> None:
    await send_summary(message, "month")


@router.callback_query(F.data.startswith("summary:"))
async def switch_summary(callback: CallbackQuery) -> None:
    period = callback.data.split(":", maxsplit=1)[1]
    await callback.answer()
    if not callback.message:
        return
    if period == "add":
        await callback.message.edit_text("Что хотите добавить?", reply_markup=add_kind_keyboard())
        return
    await send_summary(callback.message, period, edit=True)


@router.message(F.text == "⚙️ Категории")
async def manage_categories(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>Ваши категории</b>\n\n"
        "Создавайте собственные направления расходов и доходов. Что настроить?",
        reply_markup=manage_kind_keyboard(),
    )


async def show_manage_panel(message: Message, user_id: int, kind: str, edit: bool = True) -> None:
    items = await get_custom_pairs(user_id, kind)
    title = "расходов" if kind == "expense" else "доходов"
    extra = "Нажмите на категорию с 🗑, чтобы удалить её." if items else "Своих категорий пока нет."
    text = f"<b>Категории {title}</b>\n\n{extra}\nСтандартные категории удалить нельзя."
    markup = manage_categories_keyboard(kind, items)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("manage:"))
async def manage_navigation(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", maxsplit=1)[1]
    await state.clear()
    await callback.answer()
    if not callback.message:
        return
    if action in {"back", "close"}:
        if action == "close":
            await callback.message.edit_text("Настройки закрыты.")
            await callback.message.answer("Главное меню:", reply_markup=main_keyboard())
        else:
            await callback.message.edit_text("Что настроить?", reply_markup=manage_kind_keyboard())
        return
    await show_manage_panel(callback.message, callback.from_user.id, action)


@router.callback_query(F.data.startswith("manage_add:"))
async def manage_add_category(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":", maxsplit=1)[1]
    await state.set_state(ManageCategory.entering_name)
    await state.update_data(manage_kind=kind)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Новая категория</b>\n\nВведите название, например: <b>🐕 Питомец</b>.",
            reply_markup=back_cancel_keyboard(f"manage:{kind}"),
        )


@router.message(ManageCategory.entering_name)
async def save_managed_category(message: Message, state: FSMContext) -> None:
    name = normalize_category_name(message.text or "")
    if name is None:
        await message.answer("Название должно содержать от 2 до 30 символов.")
        return
    data = await state.get_data()
    kind = str(data["manage_kind"])
    current = await db.custom_categories(message.chat.id, kind)
    if len(current) >= 12:
        await message.answer("Можно создать не более 12 своих категорий в каждом разделе.")
        return
    item = await db.add_custom_category(message.chat.id, kind, name, datetime.now(timezone))
    if item is None:
        await message.answer("Такая категория уже существует. Введите другое название.")
        return
    await state.clear()
    await message.answer(f"✅ Категория {html.escape(custom_label(item.name))} добавлена.")
    await show_manage_panel(message, message.chat.id, kind, edit=False)


@router.callback_query(F.data.startswith("category_delete:"))
async def ask_category_delete(callback: CallbackQuery) -> None:
    item_id = int(callback.data.split(":", maxsplit=1)[1])
    item = await db.get_custom_category(item_id, callback.from_user.id)
    if item is None:
        await callback.answer("Категория уже удалена", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"Удалить категорию {html.escape(custom_label(item.name))}?\n\n"
            "Старые операции сохранят своё название.",
            reply_markup=confirm_category_delete_keyboard(item.id, item.kind),
        )


@router.callback_query(F.data.startswith("category_delete_confirm:"))
async def delete_category(callback: CallbackQuery) -> None:
    item_id = int(callback.data.split(":", maxsplit=1)[1])
    item = await db.get_custom_category(item_id, callback.from_user.id)
    if item is None:
        await callback.answer("Категория уже удалена", show_alert=True)
        return
    await db.delete_custom_category(item_id, callback.from_user.id)
    await callback.answer("Удалено")
    if callback.message:
        await show_manage_panel(callback.message, callback.from_user.id, item.kind)


@router.message(F.text.in_({"↩️ Удалить последнюю", "↩️ Отменить последнюю"}))
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
    deleted = await db.delete_owned(transaction_id, callback.from_user.id)
    await callback.answer("Удалено" if deleted else "Операция уже удалена")
    if callback.message:
        await callback.message.edit_text("↩️ Операция удалена." if deleted else "Эта операция уже удалена.")


@router.callback_query(F.data == "undo_cancel")
async def cancel_undo(callback: CallbackQuery) -> None:
    await callback.answer("Оставлено")
    if callback.message:
        await callback.message.edit_text("Операция сохранена без изменений.")


@router.message(AddTransaction.choosing_category)
async def category_hint(message: Message) -> None:
    await message.answer("Выберите категорию кнопкой выше или нажмите /cancel.")


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
