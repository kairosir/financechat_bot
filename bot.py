from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web
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
    admin_back_keyboard,
    admin_users_keyboard,
    account_keyboard,
    account_manage_keyboard,
    account_type_keyboard,
    accounts_manage_keyboard,
    add_kind_keyboard,
    amount_keyboard,
    back_cancel_keyboard,
    categories_keyboard,
    category_label,
    confirm_category_delete_keyboard,
    confirm_undo_keyboard,
    custom_label,
    currency_keyboard,
    deposit_daily_interest_keyboard,
    history_keyboard,
    iphone_setup_keyboard,
    main_keyboard,
    manage_categories_keyboard,
    manage_kind_keyboard,
    note_keyboard,
    summary_keyboard,
    transfer_account_keyboard,
    web_app_keyboard,
)
from shortcut_api import create_app
from updates import releases_html


router = Router()
db: Database
timezone: ZoneInfo
public_base_url: str | None
admin_ids: set[int] = set()


class AddTransaction(StatesGroup):
    choosing_category = State()
    entering_category = State()
    choosing_account = State()
    entering_amount = State()
    entering_note = State()


class ManageCategory(StatesGroup):
    entering_name = State()


class ManageAccount(StatesGroup):
    choosing_type = State()
    choosing_currency = State()
    entering_name = State()
    entering_balance = State()
    entering_interest = State()
    entering_effective_interest = State()
    choosing_daily_interest = State()
    renaming = State()
    editing_balance = State()
    choosing_transfer_from = State()
    choosing_transfer_to = State()
    entering_transfer_amount = State()


class HistoryRange(StatesGroup):
    entering_start = State()
    entering_end = State()


def parse_amount(value: str) -> int | None:
    cleaned = value.strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999.99"):
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_balance(value: str) -> int | None:
    cleaned = value.strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    limit = Decimal("999999999.99")
    if not amount.is_finite() or amount < -limit or amount > limit:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_interest_rate(value: str) -> int | None:
    cleaned = value.strip().replace("%", "").replace(",", ".")
    try:
        rate = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not rate.is_finite() or rate < 0 or rate > 100:
        return None
    return int((rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_interest_rate(rate_bps: int | None) -> str:
    if rate_bps is None:
        return ""
    value = Decimal(rate_bps) / 100
    return f"{value.normalize()}% годовых"


def format_money(amount_minor: int, currency: str = "KZT") -> str:
    value = Decimal(amount_minor) / 100
    symbol = {"KZT": "₸", "RUB": "₽", "USD": "$"}.get(currency, currency)
    if value == value.to_integral():
        return f"{int(value):,}".replace(",", " ") + f" {symbol}"
    return f"{value:,.2f}".replace(",", " ") + f" {symbol}"


def format_transaction(item: Transaction) -> str:
    sign = "−" if item.kind == "expense" else "+"
    label = html.escape(category_label(item.kind, item.category))
    date = datetime.fromisoformat(item.created_at).strftime("%d.%m · %H:%M")
    note = f"\n<i>{html.escape(item.note)}</i>" if item.note else ""
    account = f" · 💳 {html.escape(item.account_name)}" if item.account_name else ""
    return f"{date}  {label}{account}\n<b>{sign}{format_money(item.amount_minor, item.currency)}</b>{note}"


def normalize_category_name(value: str) -> str | None:
    name = " ".join(value.strip().split())
    if not 2 <= len(name) <= 30 or "|" in name:
        return None
    return name


def normalize_account_name(value: str) -> str | None:
    name = " ".join(value.strip().split())
    if not 2 <= len(name) <= 24 or "|" in name:
        return None
    return name


async def get_custom_pairs(user_id: int, kind: str) -> list[tuple[int, str]]:
    items = await db.custom_categories(user_id, kind)
    return [(item.id, item.name) for item in items]


def account_icon(account_type: str) -> str:
    return {"deposit": "🏦", "cash": "💵"}.get(account_type, "💳")


def account_type_label(account_type: str) -> str:
    return {"deposit": "Депозит", "cash": "Наличные"}.get(account_type, "Карта")


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids


async def get_account_pairs(user_id: int) -> list[tuple[int, str, int, str]]:
    items = await db.accounts(user_id)
    return [
        (item.id, f"{item.name} · {item.currency}", item.balance_minor, item.account_type)
        for item in items
    ]


async def start_transaction(message: Message, state: FSMContext, kind: str) -> None:
    await db.ensure_default_account(message.chat.id, datetime.now(timezone))
    await state.clear()
    await state.set_state(AddTransaction.choosing_category)
    await state.update_data(kind=kind)
    title = "расхода" if kind == "expense" else "дохода"
    custom = await get_custom_pairs(message.chat.id, kind)
    await message.answer("Добавляем операцию…", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        f"<b>1 из 4 · Категория {title}</b>\n\nКуда отнести операцию?",
        reply_markup=categories_keyboard(kind, custom),
    )


async def edit_category_step(message: Message, state: FSMContext, user_id: int) -> None:
    data = await state.get_data()
    kind = str(data["kind"])
    title = "расхода" if kind == "expense" else "дохода"
    custom = await get_custom_pairs(user_id, kind)
    await state.set_state(AddTransaction.choosing_category)
    await message.edit_text(
        f"<b>1 из 4 · Категория {title}</b>\n\nКуда отнести операцию?",
        reply_markup=categories_keyboard(kind, custom),
    )


async def edit_account_step(message: Message, state: FSMContext, user_id: int) -> None:
    accounts = await get_account_pairs(user_id)
    await state.set_state(AddTransaction.choosing_account)
    await message.edit_text(
        "<b>2 из 4 · Счёт</b>\n\nКакой счёт использовать?",
        reply_markup=account_keyboard(accounts),
    )


async def edit_amount_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = str(data["kind"])
    category = str(data["category"])
    await state.set_state(AddTransaction.entering_amount)
    await message.edit_text(
        "<b>3 из 4 · Сумма</b>\n\n"
        f"{html.escape(category_label(kind, category))}\n"
        f"Валюта: <b>{html.escape(str(data.get('account_currency', 'KZT')))}</b>\n"
        "Выберите или введите сумму:",
        reply_markup=amount_keyboard(),
    )


async def edit_note_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AddTransaction.entering_note)
    await message.edit_text(
        "<b>4 из 4 · Комментарий</b>\n\n"
        f"Сумма: <b>{format_money(int(data['amount_minor']), str(data.get('account_currency', 'KZT')))}</b>\n"
        "Напишите комментарий или сохраните без него:",
        reply_markup=note_keyboard(),
    )


@router.message(CommandStart())
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.ensure_default_account(message.chat.id, datetime.now(timezone))
    if message.from_user:
        display_name = " ".join(
            part for part in (message.from_user.first_name, message.from_user.last_name) if part
        )
        await db.upsert_user_profile(
            message.chat.id,
            display_name or None,
            message.from_user.username,
            datetime.now(timezone),
        )
    name = html.escape(message.from_user.first_name) if message.from_user else ""
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Здесь можно быстро записывать доходы и расходы, создавать свои категории "
        "и смотреть понятную статистику. Дата и время сохраняются автоматически.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("myid"))
async def show_my_id(message: Message) -> None:
    await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id if message.from_user else message.chat.id}</code>\n\n"
        "Он нужен только владельцу сервиса для настройки админ-доступа."
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def command_help(message: Message) -> None:
    await message.answer(
        "<b>Как пользоваться</b>\n\n"
        "➖ <b>Расход</b> и ➕ <b>Доход</b> — новая операция.\n"
        "📊 <b>Сводка</b> — баланс и основные траты.\n"
        "🕘 <b>История</b> — последние операции с фильтром.\n"
        "💳 <b>Счета</b> — остатки, новые счета и перевод средств.\n"
        "⚙️ <b>Категории</b> — свои направления доходов и расходов.\n"
        "✨ <b>Что нового?</b> — последние обновления бота.\n"
        "↩️ <b>Удалить последнюю</b> — исправить ошибочную запись.\n\n"
        "Во время добавления используйте кнопки «Назад» и «Отмена». Комментарий необязателен.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("iphone"))
@router.message(F.text == "📱 iPhone")
async def iphone_setup(message: Message) -> None:
    if not public_base_url:
        await message.answer(
            "Быстрая запись ещё не подключена к публичному адресу сервера."
        )
        return
    token = await db.rotate_shortcut_token(message.chat.id, datetime.now(timezone))
    url = f"{public_base_url}/shortcut.html?t={quote(token, safe='')}"
    await message.answer(
        "<b>Быстрая запись с iPhone</b>\n\n"
        "1. Откройте инструкцию кнопкой ниже.\n"
        "2. Один раз соберите команду из системных действий «Выбрать из меню», «Выбрать из списка» и «Запросить ввод».\n"
        "3. Добавьте её в Пункт управления.\n\n"
        "После этого сверху будут открываться небольшие нативные окна iPhone — без сайта и Telegram. "
        "Несколько последних ссылок остаются рабочими, поэтому готовая команда не сломается.",
        reply_markup=iphone_setup_keyboard(url),
    )


@router.message(Command("app"))
@router.message(F.text == "🌐 Веб-приложение")
async def open_web_app(message: Message) -> None:
    if not public_base_url:
        await message.answer("Веб-приложение ещё подключается.")
        return
    await message.answer(
        "<b>Purpose Finance</b>\n\nБаланс, счета и история в одном окне.",
        reply_markup=web_app_keyboard(public_base_url),
    )


@router.message(Command("new"))
@router.message(F.text == "✨ Что нового?")
async def whats_new(message: Message) -> None:
    await message.answer(releases_html(), reply_markup=main_keyboard())


@router.callback_query(F.data == "iphone_close")
async def iphone_close(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("Настройка iPhone закрыта.")


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
        await edit_account_step(callback.message, state, callback.from_user.id)


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
    accounts = await get_account_pairs(message.chat.id)
    await state.set_state(AddTransaction.choosing_account)
    await message.answer(
        "<b>2 из 4 · Счёт</b>\n\nКакой счёт использовать?",
        reply_markup=account_keyboard(accounts),
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


@router.callback_query(
    AddTransaction.choosing_account,
    F.data.startswith("account_select:"),
)
async def choose_account(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = int(callback.data.split(":", maxsplit=1)[1])
    account = await db.get_account(account_id, callback.from_user.id)
    if account is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    await state.update_data(
        account_id=account.id,
        account_name=account.name,
        account_currency=account.currency,
    )
    await callback.answer()
    if callback.message:
        await edit_amount_step(callback.message, state)


@router.callback_query(F.data == "flow_back:account")
async def back_to_account(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "category" not in data:
        await callback.answer("Действие уже завершено")
        return
    await callback.answer()
    if callback.message:
        await edit_account_step(callback.message, state, callback.from_user.id)


@router.callback_query(AddTransaction.entering_amount, F.data.startswith("amount:"))
async def choose_amount(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", maxsplit=1)[1]
    await callback.answer()
    if not callback.message:
        return
    if value == "custom":
        await callback.message.edit_text(
            "<b>3 из 4 · Сумма</b>\n\nВведите сумму сообщением, например: <b>1250,50</b>",
            reply_markup=back_cancel_keyboard("flow_back:account"),
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
    data = await state.get_data()
    await state.set_state(AddTransaction.entering_note)
    await message.answer(
        "<b>4 из 4 · Комментарий</b>\n\n"
        f"Сумма: <b>{format_money(amount_minor, str(data.get('account_currency', 'KZT')))}</b>\n"
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
    account_id = int(data["account_id"])
    account_name = str(data["account_name"])
    currency = str(data.get("account_currency", "KZT"))
    amount_minor = int(data["amount_minor"])
    now = datetime.now(timezone)
    await db.add_transaction(
        user_id,
        kind,
        category,
        amount_minor,
        note,
        now,
        account_id=account_id,
    )
    await state.clear()
    sign = "−" if kind == "expense" else "+"
    verb = "Расход сохранён" if kind == "expense" else "Доход сохранён"
    note_line = f"\nКомментарий: {html.escape(note)}" if note else ""
    await message.answer(
        f"✅ <b>{verb}</b>\n\n"
        f"{html.escape(category_label(kind, category))}\n"
        f"💳 {html.escape(account_name)}\n"
        f"<b>{sign}{format_money(amount_minor, currency)}</b>{note_line}\n"
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


def history_period_bounds(period: str) -> tuple[datetime | None, datetime | None, str]:
    now = datetime.now(timezone)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), None, "Сегодня"
    if period == "week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, None, "Эта неделя"
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), None, "Этот месяц"
    if period == "year":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), None, "Этот год"
    return None, None, "Всё время"


async def send_history(
    message: Message,
    period: str = "month",
    edit: bool = False,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    custom_title: str | None = None,
) -> None:
    if start_at is None and custom_title is None:
        start_at, end_at, title = history_period_bounds(period)
    else:
        title = custom_title or "Выбранный период"
    items = await db.recent(
        message.chat.id,
        limit=100,
        start_at=start_at,
        end_at=end_at,
    )
    if items:
        text = f"<b>🕘 {title}</b>\n\n" + "\n\n".join(format_transaction(item) for item in items)
    else:
        text = f"<b>🕘 {title}</b>\n\nЗа этот период операций нет."
    markup = history_keyboard(period)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == "🕘 История")
async def history(message: Message) -> None:
    await send_history(message, "month")


@router.callback_query(F.data.startswith("history_period:"))
async def switch_history(callback: CallbackQuery, state: FSMContext) -> None:
    period = callback.data.split(":", maxsplit=1)[1]
    await callback.answer()
    if period == "custom":
        await state.set_state(HistoryRange.entering_start)
        if callback.message:
            await callback.message.edit_text(
                "<b>Начало периода</b>\n\nВведите дату в формате <b>ДД.ММ.ГГГГ</b>:",
                reply_markup=back_cancel_keyboard("history_period:month"),
            )
        return
    await state.clear()
    if callback.message:
        await send_history(callback.message, period, edit=True)


def parse_history_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").replace(tzinfo=timezone)
    except ValueError:
        return None


@router.message(HistoryRange.entering_start)
async def history_custom_start(message: Message, state: FSMContext) -> None:
    start = parse_history_date(message.text or "")
    if start is None:
        await message.answer("Введите дату в формате ДД.ММ.ГГГГ, например 01.10.2026.")
        return
    await state.update_data(history_start=start.isoformat())
    await state.set_state(HistoryRange.entering_end)
    await message.answer(
        "<b>Конец периода</b>\n\nВведите последнюю дату в формате <b>ДД.ММ.ГГГГ</b>:",
        reply_markup=back_cancel_keyboard("history_period:month"),
    )


@router.message(HistoryRange.entering_end)
async def history_custom_end(message: Message, state: FSMContext) -> None:
    end_date = parse_history_date(message.text or "")
    data = await state.get_data()
    start = datetime.fromisoformat(str(data.get("history_start", "")))
    if end_date is None:
        await message.answer("Введите дату в формате ДД.ММ.ГГГГ, например 06.10.2026.")
        return
    if end_date < start:
        await message.answer("Конец периода не может быть раньше начала.")
        return
    await state.clear()
    inclusive_end = end_date + timedelta(days=1)
    title = f"{start:%d.%m.%Y} — {end_date:%d.%m.%Y}"
    await send_history(
        message,
        "custom",
        start_at=start,
        end_at=inclusive_end,
        custom_title=title,
    )


def period_start(period: str) -> tuple[datetime | None, str]:
    now = datetime.now(timezone)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), "Сегодня"
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), "Этот месяц"
    return None, "Всё время"


async def build_summary_text(user_id: int, period: str) -> str:
    start, title = period_start(period)
    totals = await db.totals_by_currency(user_id, start)
    accounts = await db.accounts(user_id)
    account_totals: dict[str, int] = {}
    for account in accounts:
        account_totals[account.currency] = account_totals.get(account.currency, 0) + account.balance_minor
    currencies = sorted(set(totals) | set(account_totals)) or ["KZT"]
    lines = [f"<b>📊 {title}</b>"]
    for currency in currencies:
        bucket = totals.get(currency, {"income": 0, "expense": 0, "categories": []})
        income_total = int(bucket["income"])
        expense_total = int(bucket["expense"])
        balance = income_total - expense_total
        sign = "+" if balance > 0 else ("−" if balance < 0 else "")
        lines.extend(
            [
                "",
                f"<b>{currency}</b>",
                f"Доходы      <b>+{format_money(income_total, currency)}</b>",
                f"Расходы     <b>−{format_money(expense_total, currency)}</b>",
                f"Баланс       <b>{sign}{format_money(abs(balance), currency)}</b>",
                f"Счета         <b>{format_money(account_totals.get(currency, 0), currency)}</b>",
            ]
        )
        for account in (item for item in accounts if item.currency == currency):
            lines.append(
                f"  {account_icon(account.account_type)} {html.escape(account.name)} — "
                f"{format_money(account.balance_minor, account.currency)}"
            )
        categories = bucket["categories"]
        if isinstance(categories, list) and categories:
            lines.append("<b>Основные расходы</b>")
            for category, total in categories[:3]:
                percent = round(total / expense_total * 100) if expense_total else 0
                label = html.escape(category_label("expense", category))
                lines.append(f"{label} — {format_money(total, currency)} · {percent}%")
    return "\n".join(lines)


async def send_summary(message: Message, period: str, edit: bool = False) -> None:
    text = await build_summary_text(message.chat.id, period)
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


async def show_admin_users(message: Message, edit: bool = False) -> None:
    users = await db.users_overview()
    text = (
        "<b>🛡 Пользователи Purpose Finance</b>\n\n"
        "Выберите пользователя для просмотра его счетов и сводки. "
        "Админ-режим доступен только ID из ADMIN_TELEGRAM_IDS."
    )
    markup = admin_users_keyboard(users)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer(
            "Админ-доступ не включён для вашего ID. Отправьте /myid владельцу сервиса."
        )
        return
    await show_admin_users(message)


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await show_admin_users(callback.message, edit=True)


@router.callback_query(F.data.startswith("admin_user:"))
async def admin_user_summary(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    target_user_id = int(callback.data.split(":", maxsplit=1)[1])
    users = await db.users_overview()
    target = next((item for item in users if int(item["user_id"]) == target_user_id), None)
    if target is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    name = html.escape(str(target.get("display_name") or target.get("username") or target_user_id))
    text = f"<b>🛡 {name}</b>\n\n" + await build_summary_text(target_user_id, "month")
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=admin_back_keyboard())


@router.callback_query(F.data == "admin_close")
async def admin_close(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("Админ-панель закрыта.")


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


async def show_accounts_panel(
    message: Message,
    user_id: int,
    edit: bool = False,
) -> None:
    await db.ensure_default_account(user_id, datetime.now(timezone))
    accounts = await db.accounts(user_id)
    lines = ["<b>💳 Ваши счета</b>", ""]
    for item in accounts:
        icon = account_icon(item.account_type)
        rate = ""
        if item.account_type == "deposit":
            rate = f" · ном. {format_interest_rate(item.interest_rate_bps)}"
            if item.effective_interest_rate_bps is not None:
                rate += f" · эфф. {format_interest_rate(item.effective_interest_rate_bps)}"
            if item.daily_interest_enabled:
                rate += " · ежедневно"
        lines.append(
            f"{icon} <b>{html.escape(item.name)}</b> — "
            f"{format_money(item.balance_minor, item.currency)}{rate}"
        )
    totals: dict[str, int] = {}
    for item in accounts:
        totals[item.currency] = totals.get(item.currency, 0) + item.balance_minor
    lines.extend(["", "<b>Итого по валютам</b>"])
    lines.extend(format_money(total, currency) for currency, total in sorted(totals.items()))
    pairs = [(item.id, item.name, item.balance_minor, item.account_type) for item in accounts]
    markup = accounts_manage_keyboard(pairs)
    if edit:
        await message.edit_text("\n".join(lines), reply_markup=markup)
    else:
        await message.answer("\n".join(lines), reply_markup=markup)


@router.message(Command("accounts"))
@router.message(F.text == "💳 Счета")
async def manage_accounts(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_accounts_panel(message, message.chat.id)


@router.callback_query(F.data == "accounts:back")
async def accounts_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback.message:
        await show_accounts_panel(callback.message, callback.from_user.id, edit=True)


@router.callback_query(F.data == "accounts:close")
async def accounts_close(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("Счета закрыты.")
        await callback.message.answer("Главное меню:", reply_markup=main_keyboard())


@router.callback_query(F.data == "accounts:add")
async def account_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    accounts = await db.accounts(callback.from_user.id)
    if len(accounts) >= 12:
        await callback.answer("Можно создать не более 12 счетов", show_alert=True)
        return
    await state.set_state(ManageAccount.choosing_type)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Новый счёт · тип</b>\n\nВыберите тип счёта:",
            reply_markup=account_type_keyboard(),
        )


@router.callback_query(ManageAccount.choosing_type, F.data.startswith("account_type:"))
async def account_add_type(callback: CallbackQuery, state: FSMContext) -> None:
    account_type = callback.data.split(":", maxsplit=1)[1]
    if account_type not in {"card", "deposit", "cash"}:
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(account_type=account_type)
    await state.set_state(ManageAccount.choosing_currency)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Новый счёт · валюта</b>\n\nВыберите валюту счёта:",
            reply_markup=currency_keyboard(),
        )


@router.callback_query(ManageAccount.choosing_currency, F.data.startswith("account_currency:"))
async def account_add_currency(callback: CallbackQuery, state: FSMContext) -> None:
    currency = callback.data.split(":", maxsplit=1)[1]
    if currency not in {"KZT", "RUB", "USD"}:
        await callback.answer("Неизвестная валюта", show_alert=True)
        return
    await state.update_data(account_currency=currency)
    await state.set_state(ManageAccount.entering_name)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Новый счёт · название</b>\n\nВведите название, например: <b>Kaspi</b>, <b>Наличные</b> или <b>Депозит</b>.",
            reply_markup=back_cancel_keyboard("accounts:back"),
        )


@router.message(ManageAccount.entering_name)
async def account_add_name(message: Message, state: FSMContext) -> None:
    name = normalize_account_name(message.text or "")
    if name is None:
        await message.answer("Название счёта должно содержать от 2 до 24 символов.")
        return
    await state.update_data(account_name=name)
    await state.set_state(ManageAccount.entering_balance)
    await message.answer(
        "<b>Начальный остаток</b>\n\nСколько денег сейчас находится на этом счёте? Можно ввести 0 или отрицательное значение.",
        reply_markup=back_cancel_keyboard("accounts:back"),
    )


@router.message(ManageAccount.entering_balance)
async def account_add_balance(message: Message, state: FSMContext) -> None:
    balance_minor = parse_balance(message.text or "")
    if balance_minor is None:
        await message.answer("Введите корректный остаток, например: 250000 или -15000.")
        return
    data = await state.get_data()
    if data.get("account_type") == "deposit":
        await state.update_data(account_balance=balance_minor)
        await state.set_state(ManageAccount.entering_interest)
        await message.answer(
            "<b>Номинальная ставка</b>\n\nВведите номинальную годовую ставку как в приложении банка, например <b>15,5</b>.",
            reply_markup=back_cancel_keyboard("accounts:back"),
        )
        return
    await save_new_account(message, state, balance_minor, None)


async def save_new_account(
    message: Message,
    state: FSMContext,
    balance_minor: int,
    interest_rate_bps: int | None,
    effective_interest_rate_bps: int | None = None,
    daily_interest_enabled: bool = False,
) -> None:
    data = await state.get_data()
    item = await db.add_account(
        message.chat.id,
        str(data["account_name"]),
        balance_minor,
        datetime.now(timezone),
        account_type=str(data.get("account_type", "card")),
        currency=str(data.get("account_currency", "KZT")),
        interest_rate_bps=interest_rate_bps,
        effective_interest_rate_bps=effective_interest_rate_bps,
        daily_interest_enabled=daily_interest_enabled,
    )
    if item is None:
        await state.set_state(ManageAccount.entering_name)
        await message.answer("Счёт с таким названием уже существует.")
        return
    await state.clear()
    await message.answer(
        f"✅ Счёт <b>{html.escape(item.name)}</b> создан с остатком "
        f"<b>{format_money(item.balance_minor, item.currency)}</b>."
    )
    await show_accounts_panel(message, message.chat.id)


@router.message(ManageAccount.entering_interest)
async def account_add_interest(message: Message, state: FSMContext) -> None:
    rate_bps = parse_interest_rate(message.text or "")
    if rate_bps is None:
        await message.answer("Введите номинальную ставку от 0 до 100, например 15,5.")
        return
    await state.update_data(interest_rate_bps=rate_bps)
    await state.set_state(ManageAccount.entering_effective_interest)
    await message.answer(
        "<b>Эффективная ставка</b>\n\nВведите эффективную годовую ставку, например <b>16,7</b>.",
        reply_markup=back_cancel_keyboard("accounts:back"),
    )


@router.message(ManageAccount.entering_effective_interest)
async def account_add_effective_interest(message: Message, state: FSMContext) -> None:
    rate_bps = parse_interest_rate(message.text or "")
    if rate_bps is None:
        await message.answer("Введите эффективную ставку от 0 до 100, например 16,7.")
        return
    await state.update_data(effective_interest_rate_bps=rate_bps)
    await state.set_state(ManageAccount.choosing_daily_interest)
    await message.answer(
        "<b>Начисление процентов</b>\n\nПроценты по депозиту начисляются каждый день?",
        reply_markup=deposit_daily_interest_keyboard(),
    )


@router.callback_query(
    ManageAccount.choosing_daily_interest,
    F.data.startswith("deposit_daily_interest:"),
)
async def account_add_daily_interest(callback: CallbackQuery, state: FSMContext) -> None:
    daily_interest_enabled = callback.data.endswith(":yes")
    data = await state.get_data()
    await callback.answer()
    if callback.message:
        await save_new_account(
            callback.message,
            state,
            int(data["account_balance"]),
            int(data["interest_rate_bps"]),
            int(data["effective_interest_rate_bps"]),
            daily_interest_enabled,
        )


@router.callback_query(F.data.startswith("account_manage:"))
async def account_manage(callback: CallbackQuery) -> None:
    account_id = int(callback.data.split(":", maxsplit=1)[1])
    item = await db.get_account(account_id, callback.from_user.id)
    if item is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        icon = account_icon(item.account_type)
        details = account_type_label(item.account_type)
        if item.account_type == "deposit":
            details += f" · номинальная {format_interest_rate(item.interest_rate_bps)}"
            if item.effective_interest_rate_bps is not None:
                details += f" · эффективная {format_interest_rate(item.effective_interest_rate_bps)}"
            details += " · проценты ежедневно" if item.daily_interest_enabled else ""
        await callback.message.edit_text(
            f"<b>{icon} {html.escape(item.name)}</b>\n\n"
            f"{details} · {item.currency}\n"
            f"Текущий остаток: <b>{format_money(item.balance_minor, item.currency)}</b>",
            reply_markup=account_manage_keyboard(item.id),
        )


@router.callback_query(F.data.startswith("account_balance:"))
async def account_balance_start(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = int(callback.data.split(":", maxsplit=1)[1])
    item = await db.get_account(account_id, callback.from_user.id)
    if item is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    await state.set_state(ManageAccount.editing_balance)
    await state.update_data(account_id=item.id, account_name=item.name, account_currency=item.currency)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"<b>Новый остаток · {html.escape(item.name)}</b>\n\nВведите фактическую сумму на счёте:",
            reply_markup=back_cancel_keyboard("accounts:back"),
        )


@router.callback_query(F.data.startswith("account_rename:"))
async def account_rename_start(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = int(callback.data.split(":", maxsplit=1)[1])
    item = await db.get_account(account_id, callback.from_user.id)
    if item is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    await state.set_state(ManageAccount.renaming)
    await state.update_data(account_id=item.id, account_name=item.name)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"<b>Переименовать · {html.escape(item.name)}</b>\n\nВведите новое название:",
            reply_markup=back_cancel_keyboard("accounts:back"),
        )


@router.message(ManageAccount.renaming)
async def account_rename_save(message: Message, state: FSMContext) -> None:
    name = normalize_account_name(message.text or "")
    if name is None:
        await message.answer("Название должно содержать от 2 до 24 символов.")
        return
    data = await state.get_data()
    changed = await db.rename_account(
        int(data["account_id"]),
        message.chat.id,
        name,
    )
    if not changed:
        await message.answer("Такое название уже используется.")
        return
    await state.clear()
    await message.answer(f"✅ Счёт переименован: <b>{html.escape(name)}</b>.")
    await show_accounts_panel(message, message.chat.id)


@router.message(ManageAccount.editing_balance)
async def account_balance_save(message: Message, state: FSMContext) -> None:
    balance_minor = parse_balance(message.text or "")
    if balance_minor is None:
        await message.answer("Введите корректную сумму.")
        return
    data = await state.get_data()
    changed = await db.set_account_balance(
        int(data["account_id"]),
        message.chat.id,
        balance_minor,
    )
    await state.clear()
    if not changed:
        await message.answer("Счёт не найден.")
        return
    await message.answer(
        f"✅ Остаток счёта <b>{html.escape(str(data['account_name']))}</b> обновлён: "
        f"<b>{format_money(balance_minor, str(data.get('account_currency', 'KZT')))}</b>."
    )
    await show_accounts_panel(message, message.chat.id)


@router.callback_query(F.data == "accounts:transfer")
async def transfer_start(callback: CallbackQuery, state: FSMContext) -> None:
    accounts = await get_account_pairs(callback.from_user.id)
    if len(accounts) < 2:
        await callback.answer("Для перевода нужно минимум два счёта", show_alert=True)
        return
    await state.set_state(ManageAccount.choosing_transfer_from)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Перевод · откуда</b>\n\nВыберите счёт списания:",
            reply_markup=transfer_account_keyboard(accounts, "from"),
        )


@router.callback_query(
    ManageAccount.choosing_transfer_from,
    F.data.startswith("transfer_from:"),
)
async def transfer_from(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = int(callback.data.split(":", maxsplit=1)[1])
    item = await db.get_account(account_id, callback.from_user.id)
    if item is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    await state.update_data(
        from_account_id=item.id,
        from_account_name=item.name,
        transfer_currency=item.currency,
    )
    await state.set_state(ManageAccount.choosing_transfer_to)
    accounts = await get_account_pairs(callback.from_user.id)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Перевод · куда</b>\n\nВыберите счёт зачисления:",
            reply_markup=transfer_account_keyboard(accounts, "to", item.id),
        )


@router.callback_query(
    ManageAccount.choosing_transfer_to,
    F.data.startswith("transfer_to:"),
)
async def transfer_to(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = int(callback.data.split(":", maxsplit=1)[1])
    item = await db.get_account(account_id, callback.from_user.id)
    if item is None:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    data = await state.get_data()
    if item.id == int(data["from_account_id"]):
        await callback.answer("Выберите другой счёт", show_alert=True)
        return
    if item.currency != str(data.get("transfer_currency", "KZT")):
        await callback.answer("Выберите счёт в той же валюте", show_alert=True)
        return
    await state.update_data(to_account_id=item.id, to_account_name=item.name)
    await state.set_state(ManageAccount.entering_transfer_amount)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"<b>Сумма перевода</b>\n\n{html.escape(str(data['from_account_name']))} → {html.escape(item.name)}\nВведите сумму:",
            reply_markup=back_cancel_keyboard("accounts:back"),
        )


@router.message(ManageAccount.entering_transfer_amount)
async def transfer_save(message: Message, state: FSMContext) -> None:
    amount_minor = parse_amount(message.text or "")
    if amount_minor is None:
        await message.answer("Введите сумму перевода больше нуля.")
        return
    data = await state.get_data()
    await db.transfer(
        message.chat.id,
        int(data["from_account_id"]),
        int(data["to_account_id"]),
        amount_minor,
        None,
        datetime.now(timezone),
    )
    await state.clear()
    await message.answer(
        "✅ Перевод выполнен\n\n"
        f"{html.escape(str(data['from_account_name']))} → {html.escape(str(data['to_account_name']))}\n"
        f"<b>{format_money(amount_minor, str(data.get('transfer_currency', 'KZT')))}</b>"
    )
    await show_accounts_panel(message, message.chat.id)


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
    global db, timezone, public_base_url, admin_ids

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
    admin_ids = {
        int(value.strip())
        for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
        if value.strip().lstrip("-").isdigit()
    }

    current_shortcut_token = os.getenv("SHORTCUT_CURRENT_TOKEN", "").strip()
    legacy_shortcut_token = os.getenv("SHORTCUT_LEGACY_TOKEN", "").strip()
    if current_shortcut_token and legacy_shortcut_token:
        await db.add_shortcut_token_alias(
            current_shortcut_token,
            legacy_shortcut_token,
            datetime.now(timezone),
        )

    configured_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    public_base_url = configured_url or (f"https://{railway_domain}" if railway_domain else None)

    api_app = create_app(db, timezone)
    api_runner = web.AppRunner(api_app, access_log=None)
    await api_runner.setup()
    api_site = web.TCPSite(api_runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await api_site.start()

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await api_runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())
