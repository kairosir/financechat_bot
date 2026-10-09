from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

import bot as bot_module
from database import Database
from keyboards import EXPENSE_CATEGORIES, INCOME_CATEGORIES, category_label, custom_label
from updates import releases_payload


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "data/finance.db")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Almaty"))
NEON_AUTH_BASE_URL = os.getenv("NEON_AUTH_BASE_URL", "").strip().rstrip("/")
NEON_AUTH_JWKS_URL = os.getenv("NEON_AUTH_JWKS_URL", "").strip()
_auth_url_parts = urlsplit(NEON_AUTH_BASE_URL)
NEON_AUTH_ISSUER = os.getenv("NEON_AUTH_ISSUER", "").strip().rstrip("/") or (
    f"{_auth_url_parts.scheme}://{_auth_url_parts.netloc}"
    if _auth_url_parts.scheme and _auth_url_parts.netloc
    else ""
)
ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if value.strip().lstrip("-").isdigit()
}
_jwks_client = PyJWKClient(NEON_AUTH_JWKS_URL, cache_keys=True) if NEON_AUTH_JWKS_URL else None

db = Database(DATABASE_URL)
bot_module.db = db
bot_module.timezone = TIMEZONE
bot_module.public_base_url = PUBLIC_BASE_URL or None
bot_module.admin_ids = ADMIN_IDS

telegram_bot = Bot(
    token=BOT_TOKEN or "000000:development-token",
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dispatcher = Dispatcher()
dispatcher.include_router(bot_module.router)

app = FastAPI(title="Purpose Finance", docs_url=None, redoc_url=None)
_initialized = False


async def ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        await db.initialize()
        _initialized = True


def verify_telegram_init_data(init_data: str) -> int | None:
    if not init_data or not BOT_TOKEN:
        return None
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        return None
    try:
        if abs(time.time() - int(fields.get("auth_date", "0"))) > 24 * 60 * 60:
            return None
    except ValueError:
        return None
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(received_hash, calculated_hash):
        return None
    try:
        return int(json.loads(fields.get("user", "{}"))["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def authorized_user(request: Request) -> int:
    await ensure_initialized()
    telegram_user_id = verify_telegram_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    auth_subject = None
    if scheme.lower() == "bearer" and token.count(".") == 2:
        auth_subject = await verify_neon_auth_token(token)
    if telegram_user_id is not None:
        if auth_subject:
            await db.bind_auth_identity(auth_subject, telegram_user_id, datetime.now(TIMEZONE))
        return telegram_user_id
    if auth_subject:
        return await db.user_by_auth_subject(auth_subject, datetime.now(TIMEZONE))
    if scheme.lower() == "bearer" and token:
        user_id = await db.user_by_shortcut_token(token)
        if user_id is not None:
            return user_id
    token = request.query_params.get("t", "")
    if token:
        user_id = await db.user_by_shortcut_token(token)
        if user_id is not None:
            return user_id
    raise HTTPException(status_code=401, detail="Откройте приложение через Purpose Finance")


async def verify_neon_auth_token(token: str) -> str | None:
    if not _jwks_client or not NEON_AUTH_ISSUER:
        return None
    try:
        signing_key = await asyncio.to_thread(_jwks_client.get_signing_key_from_jwt, token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["EdDSA"],
            issuer=NEON_AUTH_ISSUER,
            options={"verify_aud": False},
        )
    except (InvalidTokenError, PyJWKClientError, ValueError):
        return None
    subject = str(payload.get("sub", "")).strip()
    return subject or None


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def selected_user(request: Request, owner_id: int | None) -> tuple[int, int]:
    viewer_id = await authorized_user(request)
    if owner_id is None or owner_id == viewer_id:
        return viewer_id, viewer_id
    if not is_admin(viewer_id):
        raise HTTPException(status_code=403, detail="Нет доступа к данным другого пользователя")
    return viewer_id, owner_id


def parse_amount(value: object, allow_negative: bool = False) -> int | None:
    cleaned = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    limit = Decimal("999999999.99")
    if not amount.is_finite() or amount > limit:
        return None
    if allow_negative:
        if amount < -limit:
            return None
    elif amount <= 0:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_interest_rate(value: object) -> int | None:
    cleaned = str(value).strip().replace("%", "").replace(",", ".")
    try:
        rate = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not rate.is_finite() or rate < 0 or rate > 100:
        return None
    return int((rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(amount_minor: int, currency: str = "KZT") -> str:
    value = Decimal(amount_minor) / 100
    symbol = {"KZT": "₸", "RUB": "₽", "USD": "$"}.get(currency, currency)
    if value == value.to_integral():
        return f"{int(value):,}".replace(",", " ") + f" {symbol}"
    return f"{value:,.2f}".replace(",", " ") + f" {symbol}"


def period_bounds(
    period: str,
    start: str | None,
    end: str | None,
) -> tuple[datetime | None, datetime | None, str]:
    now = datetime.now(TIMEZONE)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), None, "Сегодня"
    if period == "week":
        first = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return first, None, "Эта неделя"
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), None, "Этот месяц"
    if period == "year":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), None, "Этот год"
    if period == "custom":
        try:
            first = datetime.strptime(start or "", "%Y-%m-%d").replace(tzinfo=TIMEZONE)
            last = datetime.strptime(end or "", "%Y-%m-%d").replace(tzinfo=TIMEZONE)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Укажите обе даты периода") from exc
        if last < first:
            raise HTTPException(status_code=400, detail="Конец периода раньше начала")
        return first, last + timedelta(days=1), f"{first:%d.%m.%Y} — {last:%d.%m.%Y}"
    return None, None, "Всё время"


@app.get("/api/health")
async def health() -> dict[str, object]:
    await ensure_initialized()
    return {"ok": True, "service": "purpose-finance-vercel"}


@app.get("/api/config")
async def public_config() -> dict[str, object]:
    return {
        "ok": True,
        "auth_enabled": bool(NEON_AUTH_BASE_URL and NEON_AUTH_JWKS_URL),
        "auth_url": NEON_AUTH_BASE_URL,
    }


@app.get("/api/me")
async def me(request: Request) -> dict[str, object]:
    user_id = await authorized_user(request)
    return {"ok": True, "user_id": user_id, "is_admin": is_admin(user_id)}


@app.get("/api/admin/users")
async def admin_users(request: Request) -> dict[str, object]:
    user_id = await authorized_user(request)
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="Только для администратора")
    result = []
    for item in await db.users_overview():
        target_id = int(item["user_id"])
        accounts = await db.accounts(target_id)
        result.append(
            {
                **item,
                "user_id": target_id,
                "accounts": [
                    {
                        "name": account.name,
                        "balance": money(account.balance_minor, account.currency),
                        "currency": account.currency,
                        "account_type": account.account_type,
                    }
                    for account in accounts
                ],
            }
        )
    return {"ok": True, "users": result}


@app.get("/api/whats-new")
async def whats_new() -> dict[str, object]:
    return releases_payload()


@app.post("/api/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> dict[str, bool]:
    if not WEBHOOK_SECRET or not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    await ensure_initialized()
    update = Update.model_validate(await request.json(), context={"bot": telegram_bot})
    await dispatcher.feed_update(telegram_bot, update)
    return {"ok": True}


@app.get("/api/dashboard")
async def dashboard(
    request: Request,
    period: str = "month",
    start: Optional[str] = None,
    end: Optional[str] = None,
    owner_id: Optional[int] = None,
) -> dict[str, object]:
    viewer_id, user_id = await selected_user(request, owner_id)
    now = datetime.now(TIMEZONE)
    start_at, end_at, period_label = period_bounds(period, start, end)
    await db.ensure_default_account(user_id, now)
    account_items = await db.accounts(user_id)
    totals = await db.totals_by_currency(user_id, start_at, end_at)
    history = await db.recent(user_id, limit=100, start_at=start_at, end_at=end_at)
    balances: dict[str, int] = {}
    for item in account_items:
        balances[item.currency] = balances.get(item.currency, 0) + item.balance_minor
    currencies = sorted(set(balances) | set(totals)) or ["KZT"]
    primary = totals.get("KZT", {"income": 0, "expense": 0, "categories": []})
    income = int(primary["income"])
    expense = int(primary["expense"])
    return {
        "ok": True,
        "owner_id": user_id,
        "viewer_is_admin": is_admin(viewer_id),
        "period": period,
        "period_label": period_label,
        "balance_minor": balances.get("KZT", 0),
        "balance": money(balances.get("KZT", 0)),
        "income_minor": income,
        "income": money(income),
        "expense_minor": expense,
        "expense": money(expense),
        "currency_totals": [
            {
                "currency": currency,
                "balance": money(balances.get(currency, 0), currency),
                "income": money(int(totals.get(currency, {}).get("income", 0)), currency),
                "expense": money(int(totals.get(currency, {}).get("expense", 0)), currency),
            }
            for currency in currencies
        ],
        "accounts": [
            {
                "id": item.id,
                "name": item.name,
                "balance": money(item.balance_minor, item.currency),
                "currency": item.currency,
                "account_type": item.account_type,
                "interest_rate": (
                    f"{Decimal(item.interest_rate_bps) / 100:g}%"
                    if item.interest_rate_bps is not None
                    else None
                ),
            }
            for item in account_items
        ],
        "top_categories": [
            {
                "label": category_label("expense", key),
                "amount": money(amount, currency),
                "currency": currency,
                "amount_minor": amount,
            }
            for currency in currencies
            for key, amount in (
                totals.get(currency, {}).get("categories", [])
                if isinstance(totals.get(currency, {}).get("categories", []), list)
                else []
            )
        ][:8],
        "history": [
            {
                "id": item.id,
                "kind": item.kind,
                "category": category_label(item.kind, item.category),
                "amount": money(item.amount_minor, item.currency),
                "currency": item.currency,
                "note": item.note,
                "account": item.account_name or "Основной",
                "created_at": item.created_at,
            }
            for item in history
        ],
    }


@app.get("/api/options")
@app.get("/api/shortcut/options")
@app.get("/api/v1/shortcut/options")
async def options(request: Request, kind: str = "expense") -> dict[str, object]:
    user_id = await authorized_user(request)
    if kind not in {"expense", "income"}:
        raise HTTPException(status_code=400, detail="Неизвестный тип операции")
    defaults = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    custom = await db.custom_categories(user_id, kind)
    await db.ensure_default_account(user_id, datetime.now(TIMEZONE))
    accounts = await db.accounts(user_id)
    category_items = [{"value": key, "label": label} for key, label in defaults.items()] + [
        {"value": f"custom|{item.name}", "label": custom_label(item.name)}
        for item in custom
    ]
    account_items = [
        {
            "id": item.id,
            "name": item.name,
            "label": f"{item.name} · {money(item.balance_minor, item.currency)}",
            "balance": money(item.balance_minor, item.currency),
            "currency": item.currency,
            "account_type": item.account_type,
        }
        for item in accounts
    ]
    return {
        "ok": True,
        "categories": category_items,
        "accounts": account_items,
        "category_labels": [item["label"] for item in category_items],
        "account_labels": [item["label"] for item in account_items],
    }


@app.post("/api/transactions")
@app.post("/api/shortcut/transaction")
@app.post("/api/v1/transactions")
async def create_transaction(request: Request) -> dict[str, object]:
    user_id = await authorized_user(request)
    payload = await request.json()
    kind = str(payload.get("kind", ""))
    if kind not in {"expense", "income"}:
        raise HTTPException(status_code=400, detail="Выберите расход или доход")
    amount_minor = parse_amount(payload.get("amount", ""))
    if amount_minor is None:
        raise HTTPException(status_code=400, detail="Введите корректную сумму")
    note = " ".join(str(payload.get("note", "")).strip().split()) or None
    if note and len(note) > 120:
        raise HTTPException(status_code=400, detail="Комментарий слишком длинный")
    category = str(payload.get("category", ""))
    defaults = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    custom = await db.custom_categories(user_id, kind)
    by_label = {label: key for key, label in defaults.items()}
    by_label.update({custom_label(item.name): f"custom|{item.name}" for item in custom})
    category = by_label.get(category, category)
    allowed = set(defaults) | {f"custom|{item.name}" for item in custom}
    if category not in allowed:
        raise HTTPException(status_code=400, detail="Выберите категорию")
    account = None
    account_label = str(payload.get("account", "")).strip()
    if account_label:
        for candidate in await db.accounts(user_id):
            if account_label in {
                candidate.name,
                f"{candidate.name} · {money(candidate.balance_minor, candidate.currency)}",
            }:
                account = candidate
                break
    if account is None:
        try:
            account_id = int(payload.get("account_id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Выберите счёт")
        account = await db.get_account(account_id, user_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    transaction_id = await db.add_transaction(
        user_id,
        kind,
        category,
        amount_minor,
        note,
        datetime.now(TIMEZONE),
        account_id=account.id,
    )
    return {
        "ok": True,
        "id": transaction_id,
        "category": category_label(kind, category),
        "account": account.name,
        "amount": money(amount_minor, account.currency),
    }


@app.post("/api/accounts")
async def create_account(request: Request) -> dict[str, object]:
    user_id = await authorized_user(request)
    payload = await request.json()
    name = " ".join(str(payload.get("name", "")).strip().split())
    if not 2 <= len(name) <= 24 or "|" in name:
        raise HTTPException(status_code=400, detail="Название: от 2 до 24 символов")
    balance_minor = parse_amount(payload.get("balance", "0"), allow_negative=True)
    if balance_minor is None:
        raise HTTPException(status_code=400, detail="Введите корректный остаток")
    if len(await db.accounts(user_id)) >= 12:
        raise HTTPException(status_code=400, detail="Можно создать не более 12 счетов")
    account_type = str(payload.get("account_type", "card"))
    currency = str(payload.get("currency", "KZT"))
    if account_type not in {"card", "deposit", "cash"}:
        raise HTTPException(status_code=400, detail="Выберите тип счёта")
    if currency not in {"KZT", "RUB", "USD"}:
        raise HTTPException(status_code=400, detail="Выберите валюту")
    interest_rate_bps: int | None = None
    if account_type == "deposit":
        interest_rate_bps = parse_interest_rate(payload.get("interest_rate", ""))
        if interest_rate_bps is None:
            raise HTTPException(status_code=400, detail="Ставка должна быть от 0 до 100%")
    item = await db.add_account(
        user_id,
        name,
        balance_minor,
        datetime.now(TIMEZONE),
        account_type=account_type,
        currency=currency,
        interest_rate_bps=interest_rate_bps,
    )
    if item is None:
        raise HTTPException(status_code=409, detail="Такой счёт уже существует")
    return {
        "ok": True,
        "id": item.id,
        "name": item.name,
        "balance": money(item.balance_minor, item.currency),
        "currency": item.currency,
        "account_type": item.account_type,
    }


@app.post("/api/categories")
async def create_category(request: Request) -> dict[str, object]:
    user_id = await authorized_user(request)
    payload = await request.json()
    kind = str(payload.get("kind", ""))
    name = " ".join(str(payload.get("name", "")).strip().split())
    if kind not in {"expense", "income"}:
        raise HTTPException(status_code=400, detail="Неизвестный тип")
    if not 2 <= len(name) <= 30 or "|" in name:
        raise HTTPException(status_code=400, detail="Название: от 2 до 30 символов")
    item = await db.add_custom_category(user_id, kind, name, datetime.now(TIMEZONE))
    if item is None:
        raise HTTPException(status_code=409, detail="Такая категория уже существует")
    return {"ok": True, "id": item.id, "label": custom_label(item.name)}
