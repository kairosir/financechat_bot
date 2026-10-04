from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from aiohttp import web

from database import Database
from keyboards import EXPENSE_CATEGORIES, INCOME_CATEGORIES, custom_label


MAX_AMOUNT = Decimal("999999999.99")
DATABASE_KEY = web.AppKey("database", Database)
TIMEZONE_KEY = web.AppKey("timezone", ZoneInfo)


def parse_amount(value: object) -> int | None:
    cleaned = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_balance(value: object) -> int | None:
    cleaned = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < -MAX_AMOUNT or amount > MAX_AMOUNT:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def clean_note(value: object) -> str | None:
    note = " ".join(str(value or "").strip().split())
    if not note:
        return None
    if len(note) > 120:
        raise ValueError("Комментарий должен быть не длиннее 120 символов")
    return note


def format_minor(amount_minor: int) -> str:
    value = Decimal(amount_minor) / 100
    if value == value.to_integral():
        return f"{int(value):,}".replace(",", " ") + " ₸"
    return f"{value:,.2f}".replace(",", " ") + " ₸"


async def authorized_user(request: web.Request) -> int:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise web.HTTPUnauthorized(text="Необходим персональный ключ")
    database = request.app[DATABASE_KEY]
    user_id = await database.user_by_shortcut_token(token)
    if user_id is None:
        raise web.HTTPUnauthorized(text="Ключ недействителен. Получите новую ссылку в боте")
    return user_id


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "purpose-finance"})


async def quick_page(request: web.Request) -> web.Response:
    token = request.query.get("t", "")
    database = request.app[DATABASE_KEY]
    if await database.user_by_shortcut_token(token) is None:
        raise web.HTTPUnauthorized(
            text="Ссылка недействительна. Откройте Purpose Finance и получите новую.",
            content_type="text/plain",
        )
    return web.Response(
        text=render_quick_page(token),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def categories(request: web.Request) -> web.Response:
    user_id = await authorized_user(request)
    kind = request.query.get("kind", "expense")
    if kind not in {"expense", "income"}:
        raise web.HTTPBadRequest(text="Неизвестный тип операции")
    defaults = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    items = [{"value": key, "label": label} for key, label in defaults.items()]
    database = request.app[DATABASE_KEY]
    custom = await database.custom_categories(user_id, kind)
    items.extend(
        {"value": f"custom|{item.name}", "label": custom_label(item.name)}
        for item in custom
    )
    return web.json_response({"ok": True, "kind": kind, "categories": items})


async def accounts(request: web.Request) -> web.Response:
    user_id = await authorized_user(request)
    database = request.app[DATABASE_KEY]
    timezone = request.app[TIMEZONE_KEY]
    await database.ensure_default_account(user_id, datetime.now(timezone))
    items = await database.accounts(user_id)
    return web.json_response(
        {
            "ok": True,
            "accounts": [
                {
                    "id": item.id,
                    "name": item.name,
                    "balance_minor": item.balance_minor,
                    "balance": format_minor(item.balance_minor),
                }
                for item in items
            ],
        }
    )


async def shortcut_options(request: web.Request) -> web.Response:
    """Return ready-to-display labels for the native iOS Shortcut dialogs."""
    user_id = await authorized_user(request)
    kind = request.query.get("kind", "expense")
    if kind not in {"expense", "income"}:
        raise web.HTTPBadRequest(text="Неизвестный тип операции")

    defaults = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    database = request.app[DATABASE_KEY]
    custom = await database.custom_categories(user_id, kind)
    category_labels = list(defaults.values())
    category_labels.extend(custom_label(item.name) for item in custom)

    timezone = request.app[TIMEZONE_KEY]
    await database.ensure_default_account(user_id, datetime.now(timezone))
    account_items = await database.accounts(user_id)
    return web.json_response(
        {
            "ok": True,
            "kind": kind,
            "category_labels": category_labels,
            "account_labels": [
                f"{item.name} · {format_minor(item.balance_minor)}"
                for item in account_items
            ],
        }
    )


async def add_account(request: web.Request) -> web.Response:
    user_id = await authorized_user(request)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        raise web.HTTPBadRequest(text="Ожидался JSON")
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Ожидался объект JSON")
    name = " ".join(str(payload.get("name", "")).strip().split())
    if not 2 <= len(name) <= 24 or "|" in name:
        raise web.HTTPBadRequest(text="Название должно содержать от 2 до 24 символов")
    balance_minor = parse_balance(payload.get("balance", ""))
    if balance_minor is None:
        raise web.HTTPBadRequest(text="Введите корректный остаток")
    database = request.app[DATABASE_KEY]
    current = await database.accounts(user_id)
    if len(current) >= 12:
        raise web.HTTPBadRequest(text="Можно создать не более 12 счетов")
    timezone = request.app[TIMEZONE_KEY]
    item = await database.add_account(
        user_id,
        name,
        balance_minor,
        datetime.now(timezone),
    )
    if item is None:
        raise web.HTTPConflict(text="Счёт с таким названием уже существует")
    return web.json_response(
        {
            "ok": True,
            "id": item.id,
            "name": item.name,
            "balance": format_minor(item.balance_minor),
        }
    )


async def add_transaction(request: web.Request) -> web.Response:
    user_id = await authorized_user(request)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        raise web.HTTPBadRequest(text="Ожидался JSON")
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Ожидался объект JSON")

    kind = str(payload.get("kind", ""))
    if kind not in {"expense", "income"}:
        raise web.HTTPBadRequest(text="Выберите расход или доход")

    amount_minor = parse_amount(payload.get("amount", ""))
    if amount_minor is None:
        raise web.HTTPBadRequest(text="Введите корректную сумму больше нуля")

    try:
        note = clean_note(payload.get("note"))
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error)) from error

    category = str(payload.get("category", ""))
    defaults = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    database = request.app[DATABASE_KEY]
    custom = await database.custom_categories(user_id, kind)
    by_label = {label: key for key, label in defaults.items()}
    by_label.update(
        {custom_label(item.name): f"custom|{item.name}" for item in custom}
    )
    category = by_label.get(category, category)
    allowed = set(defaults) | {f"custom|{item.name}" for item in custom}
    if category not in allowed:
        raise web.HTTPBadRequest(text="Выберите категорию из списка")

    account = None
    account_label = str(payload.get("account", "")).strip()
    if account_label:
        for candidate in await database.accounts(user_id):
            display_label = f"{candidate.name} · {format_minor(candidate.balance_minor)}"
            if account_label in {candidate.name, display_label}:
                account = candidate
                break
    if account is None:
        try:
            account_id = int(payload.get("account_id"))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="Выберите счёт")
        account = await database.get_account(account_id, user_id)
    if account is None:
        raise web.HTTPBadRequest(text="Счёт не найден")

    timezone = request.app[TIMEZONE_KEY]
    now = datetime.now(timezone)
    item_id = await database.add_transaction(
        user_id,
        kind,
        category,
        amount_minor,
        note,
        now,
        account_id=account.id,
    )
    label = defaults.get(category)
    if label is None and category.startswith("custom|"):
        label = custom_label(category.split("|", maxsplit=1)[1])
    return web.json_response(
        {
            "ok": True,
            "id": item_id,
            "kind": kind,
            "category": label or "📦 Другое",
            "account": account.name,
            "amount_minor": amount_minor,
            "created_at": now.isoformat(timespec="seconds"),
        }
    )


def create_app(database: Database, timezone: ZoneInfo) -> web.Application:
    app = web.Application(client_max_size=32 * 1024)
    app[DATABASE_KEY] = database
    app[TIMEZONE_KEY] = timezone
    app.router.add_get("/health", health)
    app.router.add_get("/iphone", quick_page)
    app.router.add_get("/api/v1/categories", categories)
    app.router.add_get("/api/v1/accounts", accounts)
    app.router.add_get("/api/v1/shortcut/options", shortcut_options)
    app.router.add_post("/api/v1/accounts", add_account)
    app.router.add_post("/api/v1/transactions", add_transaction)
    return app


def render_quick_page(token: str) -> str:
    safe_token = json.dumps(token)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
  <meta name="theme-color" content="#5b21b6">
  <title>Purpose Finance</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin:0; min-height:100vh; font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif; background:#10051f; color:#fff; }}
    main {{ max-width:520px; margin:auto; padding:22px 18px 42px; }}
    header {{ display:flex; gap:12px; align-items:center; margin-bottom:22px; }}
    .logo {{ width:46px; height:46px; display:grid; place-items:center; border-radius:15px; background:linear-gradient(145deg,#a78bfa,#6d28d9); font-size:25px; }}
    h1 {{ font-size:22px; margin:0; }} .muted {{ color:#b8a9cf; font-size:13px; margin:3px 0 0; }}
    .card {{ background:#1d0d31; border:1px solid #382054; border-radius:22px; padding:16px; box-shadow:0 18px 50px #0005; }}
    .switch {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; background:#120822; padding:5px; border-radius:15px; }}
    button {{ font:inherit; color:inherit; border:0; cursor:pointer; }}
    .switch button {{ padding:12px; border-radius:11px; background:transparent; color:#b8a9cf; font-weight:700; }}
    .switch button.active {{ background:#6d28d9; color:white; }}
    label {{ display:block; margin:18px 2px 9px; color:#d8ccea; font-size:13px; font-weight:650; }}
    .categories, .accounts {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
    .category, .account {{ min-height:46px; padding:10px; border-radius:13px; background:#2a1641; text-align:left; }}
    .category.active, .account.active {{ outline:2px solid #a78bfa; background:#42206b; }}
    .account small {{ display:block; color:#b8a9cf; margin-top:3px; }}
    input, textarea {{ width:100%; border:1px solid #493064; border-radius:14px; background:#140923; color:#fff; padding:14px; font:inherit; outline:none; }}
    input:focus, textarea:focus {{ border-color:#a78bfa; }}
    input {{ font-size:24px; font-weight:750; }} textarea {{ min-height:76px; resize:none; }}
    .save {{ width:100%; margin-top:18px; padding:15px; border-radius:15px; background:linear-gradient(135deg,#8b5cf6,#6d28d9); font-weight:800; font-size:16px; }}
    .secondary {{ width:100%; margin-top:9px; padding:11px; border-radius:13px; background:#2a1641; color:#d8ccea; }}
    .save:disabled {{ opacity:.55; }}
    .status {{ min-height:22px; margin:13px 2px 0; text-align:center; color:#c4b5fd; font-size:14px; }}
    .hint {{ margin-top:18px; padding:14px; border-radius:16px; background:#170a29; color:#ae9bc7; font-size:13px; line-height:1.45; }}
  </style>
</head>
<body><main>
  <header><div class="logo">👛</div><div><h1>Purpose Finance</h1><p class="muted">Быстрая запись без Telegram</p></div></header>
  <section class="card">
    <div class="switch"><button id="expense" class="active">− Расход</button><button id="income">+ Доход</button></div>
    <label>Категория</label><div id="categories" class="categories"></div>
    <label>Счёт</label><div id="accounts" class="accounts"></div><button id="addAccount" class="secondary">＋ Добавить счёт с остатком</button>
    <label for="amount">Сумма, ₸</label><input id="amount" inputmode="decimal" placeholder="0" autocomplete="off">
    <label for="note">Комментарий — необязательно</label><textarea id="note" maxlength="120" placeholder="Куда, откуда или зачем"></textarea>
    <button id="save" class="save">Сохранить</button><div id="status" class="status"></div>
  </section>
  <div class="hint">Добавьте эту страницу в приложение «Команды» действием «Открыть URL», а затем выберите созданную команду в Пункте управления.</div>
</main><script>
const token = {safe_token};
let kind = "expense"; let category = ""; let accountId = null;
const headers = {{"Authorization": `Bearer ${{token}}`}};
const box = document.getElementById("categories"); const accountBox = document.getElementById("accounts"); const status = document.getElementById("status");
async function loadCategories() {{
  category = ""; box.innerHTML = ""; status.textContent = "Загружаю категории…";
  const response = await fetch(`/api/v1/categories?kind=${{kind}}`, {{headers}});
  if (!response.ok) {{ status.textContent = await response.text(); return; }}
  const data = await response.json(); status.textContent = "";
  data.categories.forEach(item => {{
    const button = document.createElement("button"); button.className = "category"; button.textContent = item.label;
    button.onclick = () => {{ document.querySelectorAll(".category").forEach(x => x.classList.remove("active")); button.classList.add("active"); category = item.value; }};
    box.appendChild(button);
  }});
}}
async function loadAccounts() {{
  accountId = null; accountBox.innerHTML = "";
  const response = await fetch("/api/v1/accounts", {{headers}});
  if (!response.ok) {{ status.textContent = await response.text(); return; }}
  const data = await response.json();
  data.accounts.forEach(item => {{
    const button = document.createElement("button"); button.className = "account";
    const name = document.createElement("span"); name.textContent = `💳 ${{item.name}}`;
    const balance = document.createElement("small"); balance.textContent = item.balance;
    button.append(name, balance);
    button.onclick = () => {{ document.querySelectorAll(".account").forEach(x => x.classList.remove("active")); button.classList.add("active"); accountId = item.id; }};
    accountBox.appendChild(button);
  }});
}}
for (const next of ["expense", "income"]) document.getElementById(next).onclick = () => {{
  kind = next; document.querySelectorAll(".switch button").forEach(x => x.classList.toggle("active", x.id === kind)); loadCategories();
}};
document.getElementById("addAccount").onclick = async () => {{
  const name = prompt("Название счёта, например Kaspi или Наличные:"); if (!name) return;
  const balance = prompt("Текущий остаток на счёте:", "0"); if (balance === null) return;
  status.textContent = "Создаю счёт…";
  const response = await fetch("/api/v1/accounts", {{method:"POST", headers:{{...headers,"Content-Type":"application/json"}}, body:JSON.stringify({{name,balance}})}});
  if (!response.ok) {{ status.textContent = await response.text(); return; }}
  status.textContent = "✓ Счёт создан"; await loadAccounts();
}};
document.getElementById("save").onclick = async () => {{
  const save = document.getElementById("save"); const amount = document.getElementById("amount").value; const note = document.getElementById("note").value;
  if (!category) {{ status.textContent = "Сначала выберите категорию"; return; }}
  if (!accountId) {{ status.textContent = "Выберите счёт"; return; }}
  save.disabled = true; status.textContent = "Сохраняю…";
  try {{
    const response = await fetch("/api/v1/transactions", {{method:"POST", headers:{{...headers,"Content-Type":"application/json"}}, body:JSON.stringify({{kind,category,account_id:accountId,amount,note}})}});
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json(); const sign = kind === "expense" ? "−" : "+";
    status.textContent = `✓ ${{data.category}} · ${{data.account}} · ${{sign}}${{amount}} ₸`;
    document.getElementById("amount").value = ""; document.getElementById("note").value = ""; category = "";
    document.querySelectorAll(".category").forEach(x => x.classList.remove("active")); loadAccounts();
  }} catch (error) {{ status.textContent = error.message; }} finally {{ save.disabled = false; }}
}};
loadCategories(); loadAccounts();
</script></body></html>"""
