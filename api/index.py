from __future__ import annotations

import asyncio
from calendar import monthrange
import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
import aiohttp
from fastapi import FastAPI, Header, HTTPException, Request, Response
import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

import bot as bot_module
from database import Database
from keyboards import EXPENSE_CATEGORIES, INCOME_CATEGORIES, category_label, custom_label
from updates import releases_payload


def resolve_neon_auth_issuers(
    base_url: str,
    configured_issuer: str = "",
) -> tuple[str, ...]:
    configured = configured_issuer.strip().rstrip("/")
    if configured:
        return (configured,)
    normalized_base = base_url.strip().rstrip("/")
    parts = urlsplit(normalized_base)
    origin = (
        f"{parts.scheme}://{parts.netloc}"
        if parts.scheme and parts.netloc
        else ""
    )
    return tuple(dict.fromkeys(value for value in (normalized_base, origin) if value))


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "data/finance.db")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Almaty"))
NEON_AUTH_BASE_URL = os.getenv("NEON_AUTH_BASE_URL", "").strip().rstrip("/")
NEON_AUTH_JWKS_URL = os.getenv("NEON_AUTH_JWKS_URL", "").strip()
NEON_AUTH_ISSUERS = resolve_neon_auth_issuers(
    NEON_AUTH_BASE_URL,
    os.getenv("NEON_AUTH_ISSUER", ""),
)
ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if value.strip().lstrip("-").isdigit()
}
ADMIN_EMAILS = {
    value.strip().lower()
    for value in os.getenv("ADMIN_EMAILS", "").split(",")
    if value.strip()
}
TELEGRAM_SESSION_COOKIE = "purpose_finance_telegram"
TELEGRAM_SESSION_ISSUER = "purpose-finance-telegram"
SITE_SESSION_COOKIE = "purpose_finance_site"
SITE_SESSION_ISSUER = "purpose-finance-site"
TELEGRAM_SESSION_TTL_SECONDS = 3 * 24 * 60 * 60
_jwks_client = PyJWKClient(NEON_AUTH_JWKS_URL, cache_keys=True) if NEON_AUTH_JWKS_URL else None
_telegram_bot_username: str | None = None

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


def verify_telegram_login_data(
    fields: dict[str, object],
    bot_token: str = BOT_TOKEN,
) -> int | None:
    if not bot_token:
        return None
    received_hash = str(fields.get("hash", ""))
    if not received_hash:
        return None
    clean_fields = {
        str(key): str(value)
        for key, value in fields.items()
        if key != "hash" and value is not None
    }
    try:
        auth_date = int(clean_fields.get("auth_date", "0"))
        user_id = int(clean_fields.get("id", "0"))
    except ValueError:
        return None
    now = int(time.time())
    if user_id <= 0 or auth_date > now + 60 or now - auth_date > 10 * 60:
        return None
    data_check_string = "\n".join(
        f"{key}={clean_fields[key]}" for key in sorted(clean_fields)
    )
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return user_id if hmac.compare_digest(received_hash, calculated_hash) else None


def telegram_session_secret(bot_token: str = BOT_TOKEN) -> bytes:
    return hmac.new(
        bot_token.encode(),
        b"purpose-finance-web-session",
        hashlib.sha256,
    ).digest()


def create_telegram_session(user_id: int, bot_token: str = BOT_TOKEN) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + TELEGRAM_SESSION_TTL_SECONDS,
            "iss": TELEGRAM_SESSION_ISSUER,
            "type": "telegram_web",
        },
        telegram_session_secret(bot_token),
        algorithm="HS256",
    )


def verify_telegram_session(token: str, bot_token: str = BOT_TOKEN) -> int | None:
    if not token or not bot_token:
        return None
    try:
        payload = jwt.decode(
            token,
            telegram_session_secret(bot_token),
            algorithms=["HS256"],
            issuer=TELEGRAM_SESSION_ISSUER,
        )
        if payload.get("type") != "telegram_web":
            return None
        user_id = int(payload.get("sub", "0"))
    except (InvalidTokenError, TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def create_site_session(
    auth_subject: str,
    user_id: int,
    email: str,
    bot_token: str = BOT_TOKEN,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": auth_subject,
            "user_id": user_id,
            "email": email.strip().lower(),
            "iat": now,
            "exp": now + TELEGRAM_SESSION_TTL_SECONDS,
            "iss": SITE_SESSION_ISSUER,
            "type": "site_web",
        },
        telegram_session_secret(bot_token),
        algorithm="HS256",
    )


def verify_site_session(token: str, bot_token: str = BOT_TOKEN) -> dict[str, object] | None:
    if not token or not bot_token:
        return None
    try:
        payload = jwt.decode(
            token,
            telegram_session_secret(bot_token),
            algorithms=["HS256"],
            issuer=SITE_SESSION_ISSUER,
        )
        if payload.get("type") != "site_web":
            return None
        subject = str(payload.get("sub", "")).strip()
        user_id = int(payload.get("user_id", 0))
        email = str(payload.get("email", "")).strip().lower()
    except (InvalidTokenError, TypeError, ValueError):
        return None
    if not subject or user_id == 0 or not email:
        return None
    return {"sub": subject, "user_id": user_id, "email": email}


async def telegram_bot_username() -> str | None:
    global _telegram_bot_username
    if _telegram_bot_username:
        return _telegram_bot_username
    if not BOT_TOKEN:
        return None
    try:
        info = await telegram_bot.get_me()
    except Exception:
        return None
    _telegram_bot_username = (info.username or "").lstrip("@") or None
    return _telegram_bot_username


async def authorized_user(request: Request) -> int:
    await ensure_initialized()
    telegram_user_id = verify_telegram_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    auth_claims: dict[str, object] | None = None
    if scheme.lower() == "bearer" and token.count(".") == 2:
        auth_claims = await verify_neon_auth_claims(token)
    if telegram_user_id is not None:
        return telegram_user_id
    if auth_claims:
        return await db.user_by_auth_subject(
            str(auth_claims["sub"]),
            datetime.now(TIMEZONE),
            str(auth_claims.get("email") or "") or None,
        )
    site_session = verify_site_session(request.cookies.get(SITE_SESSION_COOKIE, ""))
    if site_session is not None:
        return int(site_session["user_id"])
    telegram_session_user = verify_telegram_session(
        request.cookies.get(TELEGRAM_SESSION_COOKIE, "")
    )
    if telegram_session_user is not None:
        return telegram_session_user
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


async def neon_auth_subject(request: Request) -> str:
    claims = await neon_auth_identity(request)
    return str(claims["sub"])


async def neon_auth_identity(request: Request) -> dict[str, object]:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token.count(".") == 2:
        claims = await verify_neon_auth_claims(token)
        if claims:
            return claims
    site_session = verify_site_session(request.cookies.get(SITE_SESSION_COOKIE, ""))
    if site_session is not None:
        return site_session
    raise HTTPException(status_code=401, detail="Сессия истекла. Войдите заново")


async def verify_neon_auth_claims(token: str) -> dict[str, object] | None:
    if not _jwks_client or not NEON_AUTH_ISSUERS:
        return None
    try:
        signing_key = await asyncio.to_thread(_jwks_client.get_signing_key_from_jwt, token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["EdDSA"],
            issuer=NEON_AUTH_ISSUERS,
            options={"verify_aud": False},
        )
    except (InvalidTokenError, PyJWKClientError, ValueError):
        return None
    subject = str(payload.get("sub", "")).strip()
    if not subject:
        return None
    return {
        "sub": subject,
        "email": str(payload.get("email", "")).strip().lower(),
    }


async def verify_neon_auth_token(token: str) -> str | None:
    claims = await verify_neon_auth_claims(token)
    return str(claims["sub"]) if claims else None


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_admin_email(email: str, allowed: set[str] | None = None) -> bool:
    configured = ADMIN_EMAILS if allowed is None else allowed
    return email.strip().lower() in configured


async def is_site_admin(request: Request) -> bool:
    try:
        claims = await neon_auth_identity(request)
    except HTTPException:
        return False
    return is_admin_email(str(claims.get("email") or ""))


async def selected_user(request: Request, owner_id: int | None) -> tuple[int, int]:
    viewer_id = await authorized_user(request)
    if owner_id is None or owner_id == viewer_id:
        return viewer_id, viewer_id
    if not await is_site_admin(request):
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


def calculated_effective_rate(nominal_rate_bps: int | None) -> str | None:
    if nominal_rate_bps is None:
        return None
    daily = Decimal(nominal_rate_bps) / Decimal(10000) / Decimal(365)
    effective = ((Decimal(1) + daily) ** 365 - Decimal(1)) * Decimal(100)
    return f"{effective.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):g}%"


def parse_optional_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Укажите корректную дату выплаты") from error


def deposit_projection(
    item: object,
    as_of: date,
) -> tuple[int, int, int | None]:
    payout_value = getattr(item, "deposit_payout_date", None)
    nominal = getattr(item, "interest_rate_bps", None)
    balance_minor = int(getattr(item, "balance_minor"))
    if not payout_value or nominal is None:
        return 0, balance_minor, None
    payout_date = next_monthly_payout(str(payout_value), as_of)
    remaining_days = max((payout_date - as_of).days, 0)
    projected_balance = balance_minor
    future_interest = 0
    current_date = as_of + timedelta(days=1)
    while current_date <= payout_date:
        days_in_year = (date(current_date.year + 1, 1, 1) - date(current_date.year, 1, 1)).days
        principal = projected_balance if bool(getattr(item, "daily_interest_enabled")) else balance_minor
        amount = int(
            (
                Decimal(principal)
                * Decimal(nominal)
                / Decimal(10000)
                / Decimal(days_in_year)
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        future_interest += max(amount, 0)
        if bool(getattr(item, "daily_interest_enabled")):
            projected_balance += max(amount, 0)
        current_date += timedelta(days=1)
    if not bool(getattr(item, "daily_interest_enabled")):
        projected_balance += future_interest
    projected_balance += int(getattr(item, "bank_accrued_interest_minor", 0))
    return future_interest, projected_balance, remaining_days


def next_monthly_payout(anchor_value: str, as_of: date) -> date:
    """Return the next recurring monthly payout date, clamped to month end."""
    payout_day = date.fromisoformat(anchor_value).day
    candidate = date(
        as_of.year,
        as_of.month,
        min(payout_day, monthrange(as_of.year, as_of.month)[1]),
    )
    if candidate >= as_of:
        return candidate
    if as_of.month == 12:
        year, month = as_of.year + 1, 1
    else:
        year, month = as_of.year, as_of.month + 1
    return date(year, month, min(payout_day, monthrange(year, month)[1]))


def account_payload(
    item: object,
    interest_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    summary = interest_summary or {}
    nominal = getattr(item, "interest_rate_bps")
    effective = getattr(item, "effective_interest_rate_bps")
    balance_minor = int(getattr(item, "balance_minor"))
    currency = str(getattr(item, "currency"))
    estimated_daily = (
        int(
            (
                Decimal(balance_minor)
                * Decimal(nominal)
                / Decimal(10000)
                / Decimal(365)
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        if nominal is not None and balance_minor > 0
        else 0
    )
    bank_accrued = int(getattr(item, "bank_accrued_interest_minor", 0))
    auto_accrued = int(summary.get("total_minor", 0))
    future_interest, projected_payout, remaining_days = deposit_projection(
        item,
        datetime.now(TIMEZONE).date(),
    )
    payout_value = getattr(item, "deposit_payout_date", None)
    next_payout = (
        next_monthly_payout(str(payout_value), datetime.now(TIMEZONE).date())
        if payout_value
        else None
    )
    return {
        "id": getattr(item, "id"),
        "name": getattr(item, "name"),
        "balance": money(balance_minor, currency),
        "balance_minor": balance_minor,
        "currency": currency,
        "account_type": getattr(item, "account_type"),
        "interest_rate": f"{Decimal(nominal) / 100:g}%" if nominal is not None else None,
        "nominal_interest_rate": f"{Decimal(nominal) / 100:g}%" if nominal is not None else None,
        "nominal_interest_rate_value": float(Decimal(nominal) / 100) if nominal is not None else None,
        "effective_interest_rate": f"{Decimal(effective) / 100:g}%" if effective is not None else None,
        "effective_interest_rate_value": float(Decimal(effective) / 100) if effective is not None else None,
        "calculated_effective_rate": calculated_effective_rate(nominal),
        "daily_interest_enabled": bool(getattr(item, "daily_interest_enabled")),
        "estimated_daily_interest": money(estimated_daily, currency),
        "interest_accrued_today": money(int(summary.get("today_minor", 0)), currency),
        "interest_accrued_total": money(int(summary.get("total_minor", 0)), currency),
        "bank_accrued_interest": money(bank_accrued, currency),
        "bank_accrued_interest_minor": bank_accrued,
        "total_tracked_interest": money(bank_accrued + auto_accrued, currency),
        "deposit_payout_date": payout_value,
        "deposit_payout_day": date.fromisoformat(str(payout_value)).day if payout_value else None,
        "deposit_payout_date_formatted": (
            date.fromisoformat(str(getattr(item, "deposit_payout_date"))).strftime("%d.%m.%Y")
            if getattr(item, "deposit_payout_date", None)
            else None
        ),
        "next_payout_date": next_payout.isoformat() if next_payout else None,
        "next_payout_date_formatted": next_payout.strftime("%d.%m.%Y") if next_payout else None,
        "days_until_payout": remaining_days,
        "projected_future_interest": money(future_interest, currency),
        "projected_payout_amount": money(projected_payout, currency),
        "last_interest_date": summary.get("last_accrual_date"),
    }


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
    bot_username = await telegram_bot_username()
    return {
        "ok": True,
        "auth_enabled": bool(NEON_AUTH_BASE_URL and NEON_AUTH_JWKS_URL),
        "auth_url": NEON_AUTH_BASE_URL,
        "telegram_login_enabled": bool(bot_username),
        "telegram_bot_username": bot_username,
    }


async def request_neon_email_auth(
    path: str,
    email: str,
    password: str,
    name: str | None = None,
) -> tuple[dict[str, object], str]:
    if not NEON_AUTH_BASE_URL:
        raise HTTPException(status_code=503, detail="Сервис входа временно недоступен")
    payload: dict[str, object] = {"email": email, "password": password}
    if name is not None:
        payload["name"] = name
    headers = {
        "Content-Type": "application/json",
        "X-Neon-Client-Info": json.dumps(
            {"sdk": "@neondatabase/auth", "version": "0.5.0-beta", "runtime": "server"}
        ),
    }
    if PUBLIC_BASE_URL:
        headers["Origin"] = PUBLIC_BASE_URL
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{NEON_AUTH_BASE_URL}{path}",
                json=payload,
                headers=headers,
            ) as result:
                data = await result.json(content_type=None)
                token = result.headers.get("set-auth-jwt", "")
                if result.status >= 400:
                    message = str(data.get("message") or data.get("error") or "") if isinstance(data, dict) else ""
                    if result.status in {400, 401, 403}:
                        raise HTTPException(status_code=401, detail="Неверный email или пароль")
                    raise HTTPException(status_code=502, detail=message or "Сервис входа временно недоступен")
            if not token:
                async with session.get(
                    f"{NEON_AUTH_BASE_URL}/token",
                    headers=headers,
                ) as token_result:
                    if token_result.status < 400:
                        token_data = await token_result.json(content_type=None)
                        if isinstance(token_data, dict):
                            token = str(token_data.get("token") or "")
    except HTTPException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Сервис входа временно недоступен") from error
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Сервис входа вернул некорректный ответ")
    session_data = data.get("session")
    if not token and isinstance(session_data, dict):
        token = str(session_data.get("token") or "")
    if not token:
        token = str(data.get("token") or "")
    return data, token


async def establish_site_email_session(
    response: Response,
    email: str,
    password: str,
    signup: bool,
    name: str | None = None,
) -> dict[str, object]:
    if signup:
        await request_neon_email_auth("/sign-up/email", email, password, name)
    _, token = await request_neon_email_auth("/sign-in/email", email, password)
    claims = await verify_neon_auth_claims(token) if token else None
    if not claims:
        raise HTTPException(status_code=401, detail="Не удалось подтвердить сессию. Попробуйте ещё раз")
    verified_email = str(claims.get("email") or email).strip().lower()
    await ensure_initialized()
    user_id = await db.user_by_auth_subject(
        str(claims["sub"]),
        datetime.now(TIMEZONE),
        verified_email,
    )
    response.set_cookie(
        SITE_SESSION_COOKIE,
        create_site_session(str(claims["sub"]), user_id, verified_email),
        max_age=TELEGRAM_SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "email": verified_email}


@app.post("/api/auth/email/login")
async def email_login(request: Request, response: Response) -> dict[str, object]:
    payload = await request.json()
    email = str(payload.get("email", "")).strip().lower() if isinstance(payload, dict) else ""
    password = str(payload.get("password", "")) if isinstance(payload, dict) else ""
    if not email or "@" not in email or len(password) < 8:
        raise HTTPException(status_code=400, detail="Проверьте email и пароль")
    return await establish_site_email_session(response, email, password, False)


@app.post("/api/auth/email/signup")
async def email_signup(request: Request, response: Response) -> dict[str, object]:
    payload = await request.json()
    email = str(payload.get("email", "")).strip().lower() if isinstance(payload, dict) else ""
    password = str(payload.get("password", "")) if isinstance(payload, dict) else ""
    name = str(payload.get("name", "")).strip() if isinstance(payload, dict) else ""
    if not email or "@" not in email or len(password) < 8:
        raise HTTPException(status_code=400, detail="Проверьте email и пароль")
    return await establish_site_email_session(
        response,
        email,
        password,
        True,
        name[:80] or email.split("@", 1)[0],
    )


@app.get("/api/auth/session")
async def validate_auth_session(request: Request) -> dict[str, bool]:
    await neon_auth_subject(request)
    return {"ok": True}


@app.post("/api/auth/telegram")
async def telegram_login(request: Request, response: Response) -> dict[str, object]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректные данные Telegram")
    user_id = verify_telegram_login_data(payload)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Не удалось подтвердить вход через Telegram")
    await ensure_initialized()
    first_name = " ".join(str(payload.get("first_name", "")).strip().split())
    last_name = " ".join(str(payload.get("last_name", "")).strip().split())
    display_name = " ".join(part for part in (first_name, last_name) if part) or None
    username = " ".join(str(payload.get("username", "")).strip().split()) or None
    await db.upsert_user_profile(
        user_id,
        display_name,
        username,
        datetime.now(TIMEZONE),
    )
    response.set_cookie(
        TELEGRAM_SESSION_COOKIE,
        create_telegram_session(user_id),
        max_age=TELEGRAM_SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "user_id": user_id}


@app.post("/api/auth/telegram/start")
async def start_telegram_login() -> dict[str, object]:
    bot_username = await telegram_bot_username()
    if not bot_username:
        raise HTTPException(status_code=503, detail="Вход через Telegram временно недоступен")
    await ensure_initialized()
    now = datetime.now(TIMEZONE)
    token = await db.create_telegram_login_request(now, now + timedelta(minutes=10))
    return {
        "ok": True,
        "expires_in": 600,
        "telegram_url": f"https://t.me/{bot_username}?start=weblogin_{token}",
    }


@app.post("/api/auth/telegram/recovery/start")
async def start_telegram_recovery(request: Request) -> dict[str, object]:
    payload = await request.json()
    email = str(payload.get("email", "")).strip().lower() if isinstance(payload, dict) else ""
    if not email or "@" not in email or len(email) > 254:
        raise HTTPException(status_code=400, detail="Укажите корректный email")
    bot_username = await telegram_bot_username()
    if not bot_username:
        raise HTTPException(status_code=503, detail="Telegram-бот временно недоступен")
    await ensure_initialized()
    now = datetime.now(TIMEZONE)
    token = await db.create_telegram_recovery_request(
        email,
        now,
        now + timedelta(minutes=10),
    )
    return {
        "ok": True,
        "token": token,
        "expires_in": 600,
        "telegram_url": f"https://t.me/{bot_username}?start=recover_{token}",
    }


@app.get("/api/auth/telegram/recovery/status")
async def telegram_recovery_status(
    request: Request,
    response: Response,
    token: str,
) -> dict[str, object]:
    await ensure_initialized()
    status = await db.telegram_recovery_status(token, datetime.now(TIMEZONE))
    if status is None:
        raise HTTPException(status_code=404, detail="Запрос восстановления не найден")
    if status["status"] == "approved" and status.get("user_id"):
        response.set_cookie(
            TELEGRAM_SESSION_COOKIE,
            create_telegram_session(int(status["user_id"])),
            max_age=TELEGRAM_SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
    return {"ok": True, "status": status["status"]}


@app.post("/api/auth/telegram/approve")
async def approve_telegram_login(request: Request, response: Response) -> dict[str, object]:
    user_id = verify_telegram_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )
    if user_id is None:
        raise HTTPException(status_code=401, detail="Откройте подтверждение внутри Telegram")
    payload = await request.json()
    token = str(payload.get("token", "")) if isinstance(payload, dict) else ""
    await ensure_initialized()
    if not await db.approve_telegram_login_request(token, user_id, datetime.now(TIMEZONE)):
        raise HTTPException(status_code=410, detail="Ссылка входа истекла. Начните вход заново")
    response.set_cookie(
        TELEGRAM_SESSION_COOKIE,
        create_telegram_session(user_id),
        max_age=TELEGRAM_SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "user_id": user_id}


@app.post("/api/auth/telegram/logout")
async def telegram_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(
        TELEGRAM_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        SITE_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request, response: Response) -> dict[str, object]:
    user_id = await authorized_user(request)
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    telegram_session_user = verify_telegram_session(
        request.cookies.get(TELEGRAM_SESSION_COOKIE, "")
    )
    site_session = verify_site_session(request.cookies.get(SITE_SESSION_COOKIE, ""))
    if telegram_session_user is not None:
        response.set_cookie(
            TELEGRAM_SESSION_COOKIE,
            create_telegram_session(telegram_session_user),
            max_age=TELEGRAM_SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
    if site_session is not None:
        response.set_cookie(
            SITE_SESSION_COOKIE,
            create_site_session(
                str(site_session["sub"]),
                int(site_session["user_id"]),
                str(site_session["email"]),
            ),
            max_age=TELEGRAM_SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
    can_sign_out = telegram_session_user is not None or (
        scheme.lower() == "bearer" and token.count(".") == 2
    ) or site_session is not None
    return {
        "ok": True,
        "user_id": user_id,
        "is_admin": await is_site_admin(request),
        "can_sign_out": can_sign_out,
        "auth_mode": "email" if site_session is not None or token.count(".") == 2 else "telegram",
        "email": str(site_session.get("email") or "") if site_session else "",
    }


@app.get("/api/profile")
async def profile(request: Request) -> dict[str, object]:
    identity = await neon_auth_identity(request)
    auth_subject = str(identity["sub"])
    await ensure_initialized()
    await db.user_by_auth_subject(
        auth_subject,
        datetime.now(TIMEZONE),
        str(identity.get("email") or "") or None,
    )
    telegram = await db.telegram_link_for_auth(auth_subject)
    return {"ok": True, "telegram": telegram}


@app.post("/api/profile/telegram/link/start")
async def start_telegram_link(request: Request) -> dict[str, object]:
    identity = await neon_auth_identity(request)
    auth_subject = str(identity["sub"])
    bot_username = await telegram_bot_username()
    if not bot_username:
        raise HTTPException(status_code=503, detail="Telegram-бот временно недоступен")
    await ensure_initialized()
    await db.user_by_auth_subject(
        auth_subject,
        datetime.now(TIMEZONE),
        str(identity.get("email") or "") or None,
    )
    if await db.telegram_link_for_auth(auth_subject):
        raise HTTPException(status_code=409, detail="Telegram уже привязан")
    now = datetime.now(TIMEZONE)
    token = await db.create_telegram_link_request(
        auth_subject,
        now,
        now + timedelta(minutes=10),
    )
    return {
        "ok": True,
        "token": token,
        "expires_in": 600,
        "telegram_url": f"https://t.me/{bot_username}?start=tglink_{token}",
    }


@app.get("/api/profile/telegram/link/status")
async def telegram_link_status(request: Request, token: str) -> dict[str, object]:
    auth_subject = await neon_auth_subject(request)
    await ensure_initialized()
    status = await db.telegram_link_request_status(token, auth_subject, datetime.now(TIMEZONE))
    if status is None:
        raise HTTPException(status_code=404, detail="Запрос привязки не найден")
    return {"ok": True, **status}


@app.post("/api/profile/telegram/unlink")
async def unlink_telegram(request: Request) -> dict[str, object]:
    auth_subject = await neon_auth_subject(request)
    await ensure_initialized()
    user_id = await db.unlink_telegram_auth(auth_subject)
    if user_id is None:
        raise HTTPException(status_code=409, detail="Telegram не привязан")
    return {"ok": True, "user_id": user_id}


@app.get("/api/admin/users")
async def admin_users(request: Request) -> dict[str, object]:
    await authorized_user(request)
    if not await is_site_admin(request):
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
                        "nominal_interest_rate": (
                            f"{Decimal(account.interest_rate_bps) / 100:g}%"
                            if account.interest_rate_bps is not None
                            else None
                        ),
                        "effective_interest_rate": (
                            f"{Decimal(account.effective_interest_rate_bps) / 100:g}%"
                            if account.effective_interest_rate_bps is not None
                            else None
                        ),
                        "daily_interest_enabled": account.daily_interest_enabled,
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
    viewer_is_admin = await is_site_admin(request)
    now = datetime.now(TIMEZONE)
    start_at, end_at, period_label = period_bounds(period, start, end)
    await db.ensure_default_account(user_id, now)
    await db.accrue_deposit_interest(user_id, now)
    account_items = await db.accounts(user_id)
    interest_summaries = await db.deposit_interest_summaries(user_id, now.date())
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
        "viewer_is_admin": viewer_is_admin,
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
        "accounts": [account_payload(item, interest_summaries.get(item.id)) for item in account_items],
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
    kind = {"Расход": "expense", "Доход": "income"}.get(kind, kind)
    if kind not in {"expense", "income"}:
        raise HTTPException(status_code=400, detail="Неизвестный тип операции")
    defaults = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    custom = await db.custom_categories(user_id, kind)
    await db.ensure_default_account(user_id, datetime.now(TIMEZONE))
    await db.accrue_deposit_interest(user_id, datetime.now(TIMEZONE))
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
    return await create_transaction_from_payload(user_id, payload)


@app.get("/api/v1/shortcut/add")
async def create_transaction_from_shortcut(request: Request) -> dict[str, object]:
    user_id = await authorized_user(request)
    return await create_transaction_from_payload(user_id, dict(request.query_params))


async def create_transaction_from_payload(
    user_id: int,
    payload: dict[str, object],
) -> dict[str, object]:
    kind = str(payload.get("kind", ""))
    kind = {"Расход": "expense", "Доход": "income"}.get(kind, kind)
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
    effective_interest_rate_bps: int | None = None
    daily_interest_enabled = False
    bank_accrued_interest_minor = 0
    deposit_payout_date: str | None = None
    if account_type == "deposit":
        interest_rate_bps = parse_interest_rate(
            payload.get("nominal_interest_rate", payload.get("interest_rate", ""))
        )
        if interest_rate_bps is None:
            raise HTTPException(status_code=400, detail="Номинальная ставка должна быть от 0 до 100%")
        effective_interest_rate_bps = parse_interest_rate(
            payload.get("effective_interest_rate", "")
        )
        if effective_interest_rate_bps is None:
            raise HTTPException(status_code=400, detail="Эффективная ставка должна быть от 0 до 100%")
        daily_interest_enabled = bool(payload.get("daily_interest_enabled", False))
        bank_accrued_interest_minor = parse_amount(
            payload.get("bank_accrued_interest", "0") or "0",
            allow_negative=True,
        )
        if bank_accrued_interest_minor is None or bank_accrued_interest_minor < 0:
            raise HTTPException(status_code=400, detail="Начисленные проценты не могут быть отрицательными")
        deposit_payout_date = parse_optional_date(payload.get("deposit_payout_date"))
    item = await db.add_account(
        user_id,
        name,
        balance_minor,
        datetime.now(TIMEZONE),
        account_type=account_type,
        currency=currency,
        interest_rate_bps=interest_rate_bps,
        effective_interest_rate_bps=effective_interest_rate_bps,
        daily_interest_enabled=daily_interest_enabled,
        bank_accrued_interest_minor=bank_accrued_interest_minor,
        deposit_payout_date=deposit_payout_date,
    )
    if item is None:
        raise HTTPException(status_code=409, detail="Такой счёт уже существует")
    return {"ok": True, **account_payload(item)}


@app.patch("/api/accounts/{account_id}")
async def edit_account(account_id: int, request: Request) -> dict[str, object]:
    user_id = await authorized_user(request)
    now = datetime.now(TIMEZONE)
    await db.accrue_deposit_interest(user_id, now)
    current = await db.get_account(account_id, user_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Некорректные данные счёта")
    name = " ".join(str(payload.get("name", current.name)).strip().split())
    if not 2 <= len(name) <= 24 or "|" in name:
        raise HTTPException(status_code=400, detail="Название: от 2 до 24 символов")
    balance_minor = parse_amount(
        payload.get("balance", Decimal(current.balance_minor) / 100),
        allow_negative=True,
    )
    if balance_minor is None:
        raise HTTPException(status_code=400, detail="Введите корректный остаток")
    account_type = str(payload.get("account_type", current.account_type))
    if account_type not in {"card", "deposit", "cash"}:
        raise HTTPException(status_code=400, detail="Выберите тип счёта")
    nominal_rate: int | None = None
    effective_rate: int | None = None
    daily_enabled = False
    bank_accrued_interest_minor = 0
    deposit_payout_date: str | None = None
    if account_type == "deposit":
        nominal_rate = parse_interest_rate(
            payload.get("nominal_interest_rate", payload.get("interest_rate", ""))
        )
        effective_rate = parse_interest_rate(payload.get("effective_interest_rate", ""))
        if nominal_rate is None:
            raise HTTPException(status_code=400, detail="Номинальная ставка должна быть от 0 до 100%")
        if effective_rate is None:
            raise HTTPException(status_code=400, detail="Эффективная ставка должна быть от 0 до 100%")
        daily_enabled = bool(payload.get("daily_interest_enabled", False))
        bank_accrued_interest_minor = parse_amount(
            payload.get(
                "bank_accrued_interest",
                Decimal(current.bank_accrued_interest_minor) / 100,
            ) or "0",
            allow_negative=True,
        )
        if bank_accrued_interest_minor is None or bank_accrued_interest_minor < 0:
            raise HTTPException(status_code=400, detail="Начисленные проценты не могут быть отрицательными")
        deposit_payout_date = parse_optional_date(
            payload.get("deposit_payout_date", current.deposit_payout_date)
        )
    updated = await db.update_account(
        account_id,
        user_id,
        name,
        balance_minor,
        account_type,
        nominal_rate,
        effective_rate,
        daily_enabled,
        now,
        bank_accrued_interest_minor,
        deposit_payout_date,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Не удалось сохранить: проверьте название счёта")
    summaries = await db.deposit_interest_summaries(user_id, now.date())
    return {"ok": True, **account_payload(updated, summaries.get(updated.id))}


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
