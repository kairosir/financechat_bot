from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

try:
    from psycopg import IntegrityError as PostgresIntegrityError
except ImportError:  # Railway's SQLite deployment does not need psycopg.
    class PostgresIntegrityError(Exception):
        pass


class _Connection:
    def __init__(self, raw: Any, postgres: bool) -> None:
        self.raw = raw
        self.postgres = postgres

    def __enter__(self) -> "_Connection":
        self.raw.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        return self.raw.__exit__(*args)

    def execute(self, query: str, params: object = ()) -> Any:
        if self.postgres:
            query = query.replace("?", "%s")
        return self.raw.execute(query, params)


@dataclass(frozen=True)
class Transaction:
    id: int
    user_id: int
    kind: str
    category: str
    amount_minor: int
    note: str | None
    created_at: str
    account_id: int | None = None
    account_name: str | None = None
    currency: str = "KZT"


@dataclass(frozen=True)
class CustomCategory:
    id: int
    user_id: int
    kind: str
    name: str
    created_at: str


@dataclass(frozen=True)
class Account:
    id: int
    user_id: int
    name: str
    balance_minor: int
    created_at: str
    account_type: str = "card"
    currency: str = "KZT"
    interest_rate_bps: int | None = None
    effective_interest_rate_bps: int | None = None
    daily_interest_enabled: bool = False
    interest_accrual_start_date: str | None = None
    bank_accrued_interest_minor: int = 0
    deposit_payout_date: str | None = None


@dataclass(frozen=True)
class AccountTransfer:
    id: int
    user_id: int
    from_account_id: int
    to_account_id: int
    amount_minor: int
    note: str | None
    created_at: str


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    display_name: str | None
    username: str | None
    created_at: str
    updated_at: str


class Database:
    def __init__(self, path: str) -> None:
        self.postgres = path.startswith(("postgres://", "postgresql://"))
        self.path = path if self.postgres else Path(path)

    async def initialize(self) -> None:
        if not self.postgres:
            assert isinstance(self.path, Path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> _Connection:
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            return _Connection(
                psycopg.connect(str(self.path), row_factory=dict_row),
                postgres=True,
            )
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return _Connection(connection, postgres=False)

    def _inserted_id(self, cursor: Any) -> int:
        if self.postgres:
            return int(cursor.fetchone()["id"])
        return int(cursor.lastrowid)

    def _initialize_sync(self) -> None:
        if self.postgres:
            self._initialize_postgres_sync()
            return
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    balance_minor INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, name)
                )
                """
            )
            account_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(accounts)")
            }
            if "account_type" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN account_type TEXT NOT NULL DEFAULT 'card'"
                )
            if "currency" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN currency TEXT NOT NULL DEFAULT 'KZT'"
                )
            if "interest_rate_bps" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN interest_rate_bps INTEGER"
                )
            if "effective_interest_rate_bps" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN effective_interest_rate_bps INTEGER"
                )
            if "daily_interest_enabled" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN daily_interest_enabled INTEGER NOT NULL DEFAULT 0"
                )
            if "interest_accrual_start_date" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN interest_accrual_start_date TEXT"
                )
            if "bank_accrued_interest_minor" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN bank_accrued_interest_minor INTEGER NOT NULL DEFAULT 0"
                )
            if "deposit_payout_date" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN deposit_payout_date TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_accounts_user
                ON accounts(user_id, id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
                    category TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            transaction_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(transactions)")
            }
            if "account_id" not in transaction_columns:
                connection.execute(
                    "ALTER TABLE transactions ADD COLUMN account_id INTEGER"
                )
            if "currency" not in transaction_columns:
                connection.execute(
                    "ALTER TABLE transactions ADD COLUMN currency TEXT NOT NULL DEFAULT 'KZT'"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_user_created
                ON transactions(user_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_account
                ON transactions(account_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deposit_interest_accruals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    accrual_date TEXT NOT NULL,
                    principal_minor INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    nominal_rate_bps INTEGER NOT NULL,
                    days_in_year INTEGER NOT NULL,
                    transaction_id INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, accrual_date)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_deposit_interest_user_date ON deposit_interest_accruals(user_id, accrual_date DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS custom_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, kind, name)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS account_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    from_account_id INTEGER NOT NULL,
                    to_account_id INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    note TEXT,
                    created_at TEXT NOT NULL,
                    CHECK(from_account_id != to_account_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_custom_categories_user_kind
                ON custom_categories(user_id, kind, id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shortcut_access (
                    user_id INTEGER PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shortcut_access_history (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shortcut_access_history_user
                ON shortcut_access_history(user_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id INTEGER PRIMARY KEY,
                    display_name TEXT,
                    username TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_identities (
                    auth_subject TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    email TEXT
                )
                """
            )
            auth_identity_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(auth_identities)")
            }
            if "email" not in auth_identity_columns:
                connection.execute("ALTER TABLE auth_identities ADD COLUMN email TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_identities_email ON auth_identities(email) WHERE email IS NOT NULL"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_login_requests (
                    token_hash TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'approved')),
                    user_id INTEGER,
                    request_type TEXT NOT NULL DEFAULT 'login',
                    auth_subject TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    approved_at TEXT
                )
                """
            )
            login_request_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(telegram_login_requests)")
            }
            if "request_type" not in login_request_columns:
                connection.execute(
                    "ALTER TABLE telegram_login_requests ADD COLUMN request_type TEXT NOT NULL DEFAULT 'login'"
                )
            if "auth_subject" not in login_request_columns:
                connection.execute(
                    "ALTER TABLE telegram_login_requests ADD COLUMN auth_subject TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telegram_login_requests_expires
                ON telegram_login_requests(expires_at)
                """
            )

    def _initialize_postgres_sync(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                balance_minor BIGINT NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, name)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id, id)",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS account_type TEXT NOT NULL DEFAULT 'card'",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'KZT'",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS interest_rate_bps INTEGER",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS effective_interest_rate_bps INTEGER",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS daily_interest_enabled BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS interest_accrual_start_date TEXT",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS bank_accrued_interest_minor BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS deposit_payout_date TEXT",
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
                category TEXT NOT NULL,
                amount_minor BIGINT NOT NULL CHECK(amount_minor > 0),
                note TEXT,
                created_at TEXT NOT NULL,
                account_id BIGINT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON transactions(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id, created_at DESC)",
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'KZT'",
            """
            CREATE TABLE IF NOT EXISTS deposit_interest_accruals (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                accrual_date TEXT NOT NULL,
                principal_minor BIGINT NOT NULL,
                amount_minor BIGINT NOT NULL,
                nominal_rate_bps INTEGER NOT NULL,
                days_in_year INTEGER NOT NULL,
                transaction_id BIGINT,
                created_at TEXT NOT NULL,
                UNIQUE(account_id, accrual_date)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_deposit_interest_user_date ON deposit_interest_accruals(user_id, accrual_date DESC)",
            """
            CREATE TABLE IF NOT EXISTS custom_categories (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, kind, name)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_custom_categories_user_kind ON custom_categories(user_id, kind, id)",
            """
            CREATE TABLE IF NOT EXISTS account_transfers (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                from_account_id BIGINT NOT NULL,
                to_account_id BIGINT NOT NULL,
                amount_minor BIGINT NOT NULL CHECK(amount_minor > 0),
                note TEXT,
                created_at TEXT NOT NULL,
                CHECK(from_account_id != to_account_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS shortcut_access (
                user_id BIGINT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS shortcut_access_history (
                token_hash TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_shortcut_access_history_user ON shortcut_access_history(user_id, created_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id BIGINT PRIMARY KEY,
                display_name TEXT,
                username TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS auth_identities (
                auth_subject TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                email TEXT
            )
            """,
            "ALTER TABLE auth_identities ADD COLUMN IF NOT EXISTS email TEXT",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_identities_email ON auth_identities(email) WHERE email IS NOT NULL",
            """
            CREATE TABLE IF NOT EXISTS telegram_login_requests (
                token_hash TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('pending', 'approved')),
                user_id BIGINT,
                request_type TEXT NOT NULL DEFAULT 'login',
                auth_subject TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                approved_at TEXT
            )
            """,
            "ALTER TABLE telegram_login_requests ADD COLUMN IF NOT EXISTS request_type TEXT NOT NULL DEFAULT 'login'",
            "ALTER TABLE telegram_login_requests ADD COLUMN IF NOT EXISTS auth_subject TEXT",
            "CREATE INDEX IF NOT EXISTS idx_telegram_login_requests_expires ON telegram_login_requests(expires_at)",
        ]
        with self._connect() as connection:
            for statement in statements:
                connection.execute(statement)

    async def add_transaction(
        self,
        user_id: int,
        kind: str,
        category: str,
        amount_minor: int,
        note: str | None,
        created_at: datetime,
        account_id: int | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._add_transaction_sync,
            user_id,
            kind,
            category,
            amount_minor,
            note,
            created_at.isoformat(timespec="seconds"),
            account_id,
        )

    def _add_transaction_sync(
        self,
        user_id: int,
        kind: str,
        category: str,
        amount_minor: int,
        note: str | None,
        created_at: str,
        account_id: int | None,
    ) -> int:
        with self._connect() as connection:
            currency = "KZT"
            if account_id is not None:
                account = connection.execute(
                    "SELECT id, currency FROM accounts WHERE id = ? AND user_id = ?",
                    (account_id, user_id),
                ).fetchone()
                if account is None:
                    raise ValueError("Счёт не найден")
                currency = str(account["currency"])
                delta = amount_minor if kind == "income" else -amount_minor
                connection.execute(
                    "UPDATE accounts SET balance_minor = balance_minor + ? WHERE id = ?",
                    (delta, account_id),
                )
            cursor = connection.execute(
                """
                INSERT INTO transactions
                    (user_id, kind, category, amount_minor, note, created_at, account_id, currency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """ + (" RETURNING id" if self.postgres else "") + """
                """,
                (user_id, kind, category, amount_minor, note, created_at, account_id, currency),
            )
            return self._inserted_id(cursor)

    async def recent(
        self,
        user_id: int,
        limit: int = 10,
        kind: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[Transaction]:
        start = start_at.isoformat(timespec="seconds") if start_at else None
        end = end_at.isoformat(timespec="seconds") if end_at else None
        return await asyncio.to_thread(self._recent_sync, user_id, limit, kind, start, end)

    def _recent_sync(
        self,
        user_id: int,
        limit: int,
        kind: str | None,
        start_at: str | None,
        end_at: str | None,
    ) -> list[Transaction]:
        condition = "t.user_id = ?"
        params: list[object] = [user_id]
        if kind:
            condition += " AND t.kind = ?"
            params.append(kind)
        if start_at:
            condition += " AND t.created_at >= ?"
            params.append(start_at)
        if end_at:
            condition += " AND t.created_at < ?"
            params.append(end_at)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT t.id, t.user_id, t.kind, t.category, t.amount_minor,
                       t.note, t.created_at, t.account_id, a.name AS account_name,
                       t.currency
                FROM transactions AS t
                LEFT JOIN accounts AS a ON a.id = t.account_id
                WHERE {condition}
                ORDER BY t.created_at DESC, t.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [Transaction(**dict(row)) for row in rows]

    async def custom_categories(
        self,
        user_id: int,
        kind: str,
    ) -> list[CustomCategory]:
        return await asyncio.to_thread(self._custom_categories_sync, user_id, kind)

    def _custom_categories_sync(
        self,
        user_id: int,
        kind: str,
    ) -> list[CustomCategory]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, kind, name, created_at
                FROM custom_categories
                WHERE user_id = ? AND kind = ?
                ORDER BY id
                """,
                (user_id, kind),
            ).fetchall()
        return [CustomCategory(**dict(row)) for row in rows]

    async def add_custom_category(
        self,
        user_id: int,
        kind: str,
        name: str,
        created_at: datetime,
    ) -> CustomCategory | None:
        return await asyncio.to_thread(
            self._add_custom_category_sync,
            user_id,
            kind,
            name,
            created_at.isoformat(timespec="seconds"),
        )

    def _add_custom_category_sync(
        self,
        user_id: int,
        kind: str,
        name: str,
        created_at: str,
    ) -> CustomCategory | None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO custom_categories (user_id, kind, name, created_at)
                    VALUES (?, ?, ?, ?)
                    """ + (" RETURNING id" if self.postgres else "") + """
                    """,
                    (user_id, kind, name, created_at),
                )
                item_id = self._inserted_id(cursor)
        except (sqlite3.IntegrityError, PostgresIntegrityError):
            return None
        return CustomCategory(item_id, user_id, kind, name, created_at)

    async def get_custom_category(
        self,
        item_id: int,
        user_id: int,
    ) -> CustomCategory | None:
        return await asyncio.to_thread(self._get_custom_category_sync, item_id, user_id)

    def _get_custom_category_sync(
        self,
        item_id: int,
        user_id: int,
    ) -> CustomCategory | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, user_id, kind, name, created_at
                FROM custom_categories
                WHERE id = ? AND user_id = ?
                """,
                (item_id, user_id),
            ).fetchone()
        return CustomCategory(**dict(row)) if row else None

    async def delete_custom_category(self, item_id: int, user_id: int) -> bool:
        return await asyncio.to_thread(
            self._delete_custom_category_sync,
            item_id,
            user_id,
        )

    def _delete_custom_category_sync(self, item_id: int, user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM custom_categories WHERE id = ? AND user_id = ?",
                (item_id, user_id),
            )
            return cursor.rowcount == 1

    async def ensure_default_account(
        self,
        user_id: int,
        created_at: datetime,
    ) -> Account:
        return await asyncio.to_thread(
            self._ensure_default_account_sync,
            user_id,
            created_at.isoformat(timespec="seconds"),
        )

    def _ensure_default_account_sync(self, user_id: int, created_at: str) -> Account:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, user_id, name, balance_minor, created_at,
                       account_type, currency, interest_rate_bps,
                       effective_interest_rate_bps, daily_interest_enabled,
                       interest_accrual_start_date, bank_accrued_interest_minor,
                       deposit_payout_date
                FROM accounts WHERE user_id = ? ORDER BY id LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts
                        (user_id, name, balance_minor, created_at, account_type, currency)
                    VALUES (?, 'Основной', 0, ?, 'card', 'KZT')
                    """ + (" RETURNING id" if self.postgres else "") + """
                    """,
                    (user_id, created_at),
                )
                return Account(self._inserted_id(cursor), user_id, "Основной", 0, created_at)
        return Account(**dict(row))

    async def accounts(self, user_id: int) -> list[Account]:
        return await asyncio.to_thread(self._accounts_sync, user_id)

    def _accounts_sync(self, user_id: int) -> list[Account]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, name, balance_minor, created_at,
                       account_type, currency, interest_rate_bps,
                       effective_interest_rate_bps, daily_interest_enabled,
                       interest_accrual_start_date, bank_accrued_interest_minor,
                       deposit_payout_date
                FROM accounts WHERE user_id = ? ORDER BY id
                """,
                (user_id,),
            ).fetchall()
        return [Account(**dict(row)) for row in rows]

    async def get_account(self, account_id: int, user_id: int) -> Account | None:
        return await asyncio.to_thread(self._get_account_sync, account_id, user_id)

    def _get_account_sync(self, account_id: int, user_id: int) -> Account | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, user_id, name, balance_minor, created_at,
                       account_type, currency, interest_rate_bps,
                       effective_interest_rate_bps, daily_interest_enabled,
                       interest_accrual_start_date, bank_accrued_interest_minor,
                       deposit_payout_date
                FROM accounts WHERE id = ? AND user_id = ?
                """,
                (account_id, user_id),
            ).fetchone()
        return Account(**dict(row)) if row else None

    async def add_account(
        self,
        user_id: int,
        name: str,
        balance_minor: int,
        created_at: datetime,
        account_type: str = "card",
        currency: str = "KZT",
        interest_rate_bps: int | None = None,
        effective_interest_rate_bps: int | None = None,
        daily_interest_enabled: bool = False,
        bank_accrued_interest_minor: int = 0,
        deposit_payout_date: str | None = None,
    ) -> Account | None:
        return await asyncio.to_thread(
            self._add_account_sync,
            user_id,
            name,
            balance_minor,
            created_at.isoformat(timespec="seconds"),
            account_type,
            currency,
            interest_rate_bps,
            effective_interest_rate_bps,
            daily_interest_enabled,
            (
                created_at.date().isoformat()
                if account_type == "deposit" and daily_interest_enabled
                else None
            ),
            bank_accrued_interest_minor if account_type == "deposit" else 0,
            deposit_payout_date if account_type == "deposit" else None,
        )

    def _add_account_sync(
        self,
        user_id: int,
        name: str,
        balance_minor: int,
        created_at: str,
        account_type: str,
        currency: str,
        interest_rate_bps: int | None,
        effective_interest_rate_bps: int | None,
        daily_interest_enabled: bool,
        interest_accrual_start_date: str | None,
        bank_accrued_interest_minor: int,
        deposit_payout_date: str | None,
    ) -> Account | None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts
                        (user_id, name, balance_minor, created_at, account_type, currency,
                         interest_rate_bps, effective_interest_rate_bps, daily_interest_enabled,
                         interest_accrual_start_date, bank_accrued_interest_minor,
                         deposit_payout_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """ + (" RETURNING id" if self.postgres else "") + """
                    """,
                    (
                        user_id, name, balance_minor, created_at, account_type, currency,
                        interest_rate_bps, effective_interest_rate_bps, daily_interest_enabled,
                        interest_accrual_start_date, bank_accrued_interest_minor,
                        deposit_payout_date,
                    ),
                )
                account_id = self._inserted_id(cursor)
        except (sqlite3.IntegrityError, PostgresIntegrityError):
            return None
        return Account(
            account_id,
            user_id,
            name,
            balance_minor,
            created_at,
            account_type,
            currency,
            interest_rate_bps,
            effective_interest_rate_bps,
            daily_interest_enabled,
            interest_accrual_start_date,
            bank_accrued_interest_minor,
            deposit_payout_date,
        )

    async def set_account_balance(
        self,
        account_id: int,
        user_id: int,
        balance_minor: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._set_account_balance_sync,
            account_id,
            user_id,
            balance_minor,
        )

    def _set_account_balance_sync(
        self,
        account_id: int,
        user_id: int,
        balance_minor: int,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts SET balance_minor = ?
                WHERE id = ? AND user_id = ?
                """,
                (balance_minor, account_id, user_id),
            )
            return cursor.rowcount == 1

    async def rename_account(
        self,
        account_id: int,
        user_id: int,
        name: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._rename_account_sync,
            account_id,
            user_id,
            name,
        )

    def _rename_account_sync(
        self,
        account_id: int,
        user_id: int,
        name: str,
    ) -> bool:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE accounts SET name = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (name, account_id, user_id),
                )
                return cursor.rowcount == 1
        except (sqlite3.IntegrityError, PostgresIntegrityError):
            return False

    async def update_account(
        self,
        account_id: int,
        user_id: int,
        name: str,
        balance_minor: int,
        account_type: str,
        interest_rate_bps: int | None,
        effective_interest_rate_bps: int | None,
        daily_interest_enabled: bool,
        updated_at: datetime,
        bank_accrued_interest_minor: int = 0,
        deposit_payout_date: str | None = None,
    ) -> Account | None:
        return await asyncio.to_thread(
            self._update_account_sync,
            account_id,
            user_id,
            name,
            balance_minor,
            account_type,
            interest_rate_bps,
            effective_interest_rate_bps,
            daily_interest_enabled,
            updated_at.date().isoformat(),
            bank_accrued_interest_minor,
            deposit_payout_date,
        )

    def _update_account_sync(
        self,
        account_id: int,
        user_id: int,
        name: str,
        balance_minor: int,
        account_type: str,
        interest_rate_bps: int | None,
        effective_interest_rate_bps: int | None,
        daily_interest_enabled: bool,
        updated_on: str,
        bank_accrued_interest_minor: int,
        deposit_payout_date: str | None,
    ) -> Account | None:
        if account_type != "deposit":
            interest_rate_bps = None
            effective_interest_rate_bps = None
            daily_interest_enabled = False
            bank_accrued_interest_minor = 0
            deposit_payout_date = None
        try:
            with self._connect() as connection:
                current = connection.execute(
                    """
                    SELECT interest_accrual_start_date, account_type, daily_interest_enabled
                    FROM accounts WHERE id = ? AND user_id = ?
                    """,
                    (account_id, user_id),
                ).fetchone()
                if current is None:
                    return None
                start_date = None
                if account_type == "deposit" and daily_interest_enabled:
                    start_date = (
                        current["interest_accrual_start_date"]
                        if current["account_type"] == "deposit"
                        and bool(current["daily_interest_enabled"])
                        and current["interest_accrual_start_date"]
                        else updated_on
                    )
                cursor = connection.execute(
                    """
                    UPDATE accounts
                    SET name = ?, balance_minor = ?, account_type = ?,
                        interest_rate_bps = ?, effective_interest_rate_bps = ?,
                        daily_interest_enabled = ?, interest_accrual_start_date = ?,
                        bank_accrued_interest_minor = ?, deposit_payout_date = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        name,
                        balance_minor,
                        account_type,
                        interest_rate_bps,
                        effective_interest_rate_bps,
                        daily_interest_enabled,
                        start_date,
                        bank_accrued_interest_minor,
                        deposit_payout_date,
                        account_id,
                        user_id,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
        except (sqlite3.IntegrityError, PostgresIntegrityError):
            return None
        return self._get_account_sync(account_id, user_id)

    async def accrue_deposit_interest(
        self,
        user_id: int,
        as_of: datetime,
    ) -> dict[int, int]:
        return await asyncio.to_thread(
            self._accrue_deposit_interest_sync,
            user_id,
            as_of,
        )

    def _accrue_deposit_interest_sync(
        self,
        user_id: int,
        as_of: datetime,
    ) -> dict[int, int]:
        today = as_of.date()
        credited: dict[int, int] = {}
        with self._connect() as connection:
            lock = " FOR UPDATE" if self.postgres else ""
            rows = connection.execute(
                """
                SELECT id, balance_minor, currency, interest_rate_bps,
                       interest_accrual_start_date
                FROM accounts
                WHERE user_id = ? AND account_type = 'deposit'
                  AND daily_interest_enabled = """
                + ("TRUE" if self.postgres else "1")
                + " AND interest_rate_bps IS NOT NULL"
                + lock,
                (user_id,),
            ).fetchall()
            for row in rows:
                account_id = int(row["id"])
                start_value = row["interest_accrual_start_date"]
                if not start_value:
                    connection.execute(
                        "UPDATE accounts SET interest_accrual_start_date = ? WHERE id = ?",
                        (today.isoformat(), account_id),
                    )
                    continue
                start_date = date.fromisoformat(str(start_value))
                latest = connection.execute(
                    "SELECT MAX(accrual_date) AS value FROM deposit_interest_accruals WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                last_date = date.fromisoformat(str(latest["value"])) if latest and latest["value"] else start_date
                current_date = last_date + timedelta(days=1)
                balance_minor = int(row["balance_minor"])
                nominal_rate_bps = int(row["interest_rate_bps"])
                while current_date <= today:
                    days_in_year = (date(current_date.year + 1, 1, 1) - date(current_date.year, 1, 1)).days
                    amount_minor = int(
                        (
                            Decimal(balance_minor)
                            * Decimal(nominal_rate_bps)
                            / Decimal(10000)
                            / Decimal(days_in_year)
                        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    )
                    transaction_id = None
                    if amount_minor > 0:
                        balance_minor += amount_minor
                        cursor = connection.execute(
                            """
                            INSERT INTO transactions
                                (user_id, kind, category, amount_minor, note, created_at, account_id, currency)
                            VALUES (?, 'income', 'deposit_interest', ?, ?, ?, ?, ?)
                            """ + (" RETURNING id" if self.postgres else ""),
                            (
                                user_id,
                                amount_minor,
                                f"Автоначисление за {current_date:%d.%m.%Y} · номинальная {Decimal(nominal_rate_bps) / 100:g}% / {days_in_year}",
                                as_of.replace(
                                    year=current_date.year,
                                    month=current_date.month,
                                    day=current_date.day,
                                    hour=0,
                                    minute=1,
                                    second=0,
                                    microsecond=0,
                                ).isoformat(timespec="seconds"),
                                account_id,
                                str(row["currency"]),
                            ),
                        )
                        transaction_id = self._inserted_id(cursor)
                        connection.execute(
                            "UPDATE accounts SET balance_minor = ? WHERE id = ?",
                            (balance_minor, account_id),
                        )
                        credited[account_id] = credited.get(account_id, 0) + amount_minor
                    connection.execute(
                        """
                        INSERT INTO deposit_interest_accruals
                            (account_id, user_id, accrual_date, principal_minor, amount_minor,
                             nominal_rate_bps, days_in_year, transaction_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            account_id,
                            user_id,
                            current_date.isoformat(),
                            balance_minor - amount_minor,
                            amount_minor,
                            nominal_rate_bps,
                            days_in_year,
                            transaction_id,
                            as_of.isoformat(timespec="seconds"),
                        ),
                    )
                    current_date += timedelta(days=1)
        return credited

    async def deposit_interest_summaries(
        self,
        user_id: int,
        today: date,
    ) -> dict[int, dict[str, object]]:
        return await asyncio.to_thread(
            self._deposit_interest_summaries_sync,
            user_id,
            today.isoformat(),
        )

    def _deposit_interest_summaries_sync(
        self,
        user_id: int,
        today: str,
    ) -> dict[int, dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT account_id,
                       COALESCE(SUM(amount_minor), 0) AS total_minor,
                       COALESCE(SUM(CASE WHEN accrual_date = ? THEN amount_minor ELSE 0 END), 0) AS today_minor,
                       MAX(accrual_date) AS last_accrual_date
                FROM deposit_interest_accruals
                WHERE user_id = ?
                GROUP BY account_id
                """,
                (today, user_id),
            ).fetchall()
        return {
            int(row["account_id"]): {
                "total_minor": int(row["total_minor"]),
                "today_minor": int(row["today_minor"]),
                "last_accrual_date": row["last_accrual_date"],
            }
            for row in rows
        }

    async def transfer(
        self,
        user_id: int,
        from_account_id: int,
        to_account_id: int,
        amount_minor: int,
        note: str | None,
        created_at: datetime,
    ) -> int:
        return await asyncio.to_thread(
            self._transfer_sync,
            user_id,
            from_account_id,
            to_account_id,
            amount_minor,
            note,
            created_at.isoformat(timespec="seconds"),
        )

    def _transfer_sync(
        self,
        user_id: int,
        from_account_id: int,
        to_account_id: int,
        amount_minor: int,
        note: str | None,
        created_at: str,
    ) -> int:
        if from_account_id == to_account_id:
            raise ValueError("Выберите разные счета")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, currency FROM accounts WHERE user_id = ? AND id IN (?, ?)",
                (user_id, from_account_id, to_account_id),
            ).fetchall()
            if len(rows) != 2:
                raise ValueError("Счёт не найден")
            if len({str(row["currency"]) for row in rows}) != 1:
                raise ValueError("Переводы доступны только между счетами в одной валюте")
            connection.execute(
                "UPDATE accounts SET balance_minor = balance_minor - ? WHERE id = ?",
                (amount_minor, from_account_id),
            )
            connection.execute(
                "UPDATE accounts SET balance_minor = balance_minor + ? WHERE id = ?",
                (amount_minor, to_account_id),
            )
            cursor = connection.execute(
                """
                INSERT INTO account_transfers
                    (user_id, from_account_id, to_account_id, amount_minor, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """ + (" RETURNING id" if self.postgres else "") + """
                """,
                (
                    user_id,
                    from_account_id,
                    to_account_id,
                    amount_minor,
                    note,
                    created_at,
                ),
            )
            return self._inserted_id(cursor)

    async def totals(
        self,
        user_id: int,
        start_at: datetime | None = None,
    ) -> tuple[int, int, list[tuple[str, int]]]:
        start = start_at.isoformat(timespec="seconds") if start_at else None
        return await asyncio.to_thread(self._totals_sync, user_id, start)

    def _totals_sync(
        self,
        user_id: int,
        start_at: str | None,
    ) -> tuple[int, int, list[tuple[str, int]]]:
        condition = "user_id = ?"
        params: list[object] = [user_id]
        if start_at:
            condition += " AND created_at >= ?"
            params.append(start_at)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT kind, COALESCE(SUM(amount_minor), 0) AS total
                FROM transactions
                WHERE {condition}
                GROUP BY kind
                """,
                params,
            ).fetchall()
            categories = connection.execute(
                f"""
                SELECT category, SUM(amount_minor) AS total
                FROM transactions
                WHERE {condition} AND kind = 'expense'
                GROUP BY category
                ORDER BY total DESC
                LIMIT 5
                """,
                params,
            ).fetchall()

        by_kind = {row["kind"]: int(row["total"]) for row in rows}
        category_totals = [
            (str(row["category"]), int(row["total"])) for row in categories
        ]
        return by_kind.get("income", 0), by_kind.get("expense", 0), category_totals

    async def totals_by_currency(
        self,
        user_id: int,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, dict[str, object]]:
        start = start_at.isoformat(timespec="seconds") if start_at else None
        end = end_at.isoformat(timespec="seconds") if end_at else None
        return await asyncio.to_thread(
            self._totals_by_currency_sync,
            user_id,
            start,
            end,
        )

    def _totals_by_currency_sync(
        self,
        user_id: int,
        start_at: str | None,
        end_at: str | None,
    ) -> dict[str, dict[str, object]]:
        condition = "user_id = ?"
        params: list[object] = [user_id]
        if start_at:
            condition += " AND created_at >= ?"
            params.append(start_at)
        if end_at:
            condition += " AND created_at < ?"
            params.append(end_at)
        with self._connect() as connection:
            totals = connection.execute(
                f"""
                SELECT currency, kind, COALESCE(SUM(amount_minor), 0) AS total
                FROM transactions
                WHERE {condition}
                GROUP BY currency, kind
                """,
                params,
            ).fetchall()
            categories = connection.execute(
                f"""
                SELECT currency, category, SUM(amount_minor) AS total
                FROM transactions
                WHERE {condition} AND kind = 'expense'
                GROUP BY currency, category
                ORDER BY currency, total DESC
                """,
                params,
            ).fetchall()
        result: dict[str, dict[str, object]] = {}
        for row in totals:
            currency = str(row["currency"])
            result.setdefault(currency, {"income": 0, "expense": 0, "categories": []})
            result[currency][str(row["kind"])] = int(row["total"])
        for row in categories:
            currency = str(row["currency"])
            bucket = result.setdefault(
                currency,
                {"income": 0, "expense": 0, "categories": []},
            )
            category_items = bucket["categories"]
            assert isinstance(category_items, list)
            if len(category_items) < 5:
                category_items.append((str(row["category"]), int(row["total"])))
        return result

    async def last(self, user_id: int) -> Transaction | None:
        items = await self.recent(user_id, limit=1)
        return items[0] if items else None

    async def delete_owned(self, transaction_id: int, user_id: int) -> bool:
        return await asyncio.to_thread(
            self._delete_owned_sync,
            transaction_id,
            user_id,
        )

    def _delete_owned_sync(self, transaction_id: int, user_id: int) -> bool:
        with self._connect() as connection:
            item = connection.execute(
                """
                SELECT kind, category, amount_minor, account_id FROM transactions
                WHERE id = ? AND user_id = ?
                """,
                (transaction_id, user_id),
            ).fetchone()
            if item is None:
                return False
            if item["category"] == "deposit_interest":
                return False
            cursor = connection.execute(
                "DELETE FROM transactions WHERE id = ? AND user_id = ?",
                (transaction_id, user_id),
            )
            if cursor.rowcount == 1 and item["account_id"] is not None:
                reverse_delta = (
                    -int(item["amount_minor"])
                    if item["kind"] == "income"
                    else int(item["amount_minor"])
                )
                connection.execute(
                    "UPDATE accounts SET balance_minor = balance_minor + ? WHERE id = ?",
                    (reverse_delta, int(item["account_id"])),
                )
            return cursor.rowcount == 1

    async def rotate_shortcut_token(self, user_id: int, created_at: datetime) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        await asyncio.to_thread(
            self._store_shortcut_token_sync,
            user_id,
            token_hash,
            created_at.isoformat(timespec="seconds"),
        )
        return token

    def _store_shortcut_token_sync(
        self,
        user_id: int,
        token_hash: str,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT token_hash, created_at FROM shortcut_access WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if previous is not None:
                connection.execute(
                    """
                    INSERT INTO shortcut_access_history
                        (token_hash, user_id, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(token_hash) DO NOTHING
                    """,
                    (str(previous["token_hash"]), user_id, str(previous["created_at"])),
                )
            connection.execute(
                """
                INSERT INTO shortcut_access (user_id, token_hash, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    token_hash = excluded.token_hash,
                    created_at = excluded.created_at
                """,
                (user_id, token_hash, created_at),
            )
            connection.execute(
                """
                DELETE FROM shortcut_access_history
                WHERE user_id = ? AND token_hash NOT IN (
                    SELECT token_hash FROM shortcut_access_history
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT 5
                )
                """,
                (user_id, user_id),
            )

    async def add_shortcut_token_alias(
        self,
        current_token: str,
        alias_token: str,
        created_at: datetime,
    ) -> bool:
        user_id = await self.user_by_shortcut_token(current_token)
        if user_id is None or not alias_token or len(alias_token) > 256:
            return False
        alias_hash = hashlib.sha256(alias_token.encode("utf-8")).hexdigest()
        await asyncio.to_thread(
            self._add_shortcut_token_alias_sync,
            user_id,
            alias_hash,
            created_at.isoformat(timespec="seconds"),
        )
        return True

    def _add_shortcut_token_alias_sync(
        self,
        user_id: int,
        token_hash: str,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO shortcut_access_history
                    (token_hash, user_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(token_hash) DO UPDATE SET
                    user_id = excluded.user_id,
                    created_at = excluded.created_at
                """,
                (token_hash, user_id, created_at),
            )

    async def user_by_shortcut_token(self, token: str) -> int | None:
        if not token or len(token) > 256:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return await asyncio.to_thread(
            self._user_by_shortcut_token_sync,
            token_hash,
        )

    def _user_by_shortcut_token_sync(self, token_hash: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM shortcut_access WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT user_id FROM shortcut_access_history WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
        return int(row["user_id"]) if row else None

    async def create_telegram_login_request(
        self,
        created_at: datetime,
        expires_at: datetime,
    ) -> str:
        token = secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        await asyncio.to_thread(
            self._create_telegram_login_request_sync,
            token_hash,
            created_at.isoformat(timespec="seconds"),
            expires_at.isoformat(timespec="seconds"),
        )
        return token

    def _create_telegram_login_request_sync(
        self,
        token_hash: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM telegram_login_requests WHERE expires_at < ?",
                (created_at,),
            )
            connection.execute(
                """
                INSERT INTO telegram_login_requests
                    (token_hash, status, user_id, request_type, auth_subject, created_at, expires_at, approved_at)
                VALUES (?, 'pending', NULL, 'login', NULL, ?, ?, NULL)
                """,
                (token_hash, created_at, expires_at),
            )

    async def approve_telegram_login_request(
        self,
        token: str,
        user_id: int,
        approved_at: datetime,
    ) -> bool:
        if not token or len(token) > 128:
            return False
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return await asyncio.to_thread(
            self._approve_telegram_login_request_sync,
            token_hash,
            user_id,
            approved_at.isoformat(timespec="seconds"),
        )

    def _approve_telegram_login_request_sync(
        self,
        token_hash: str,
        user_id: int,
        approved_at: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_login_requests
                SET status = 'approved', user_id = ?, approved_at = ?
                WHERE token_hash = ? AND request_type = 'login'
                  AND status = 'pending' AND expires_at >= ?
                """,
                (user_id, approved_at, token_hash, approved_at),
            )
            return cursor.rowcount == 1

    async def create_telegram_recovery_request(
        self,
        email: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> str:
        token = secrets.token_urlsafe(24)
        await asyncio.to_thread(
            self._create_telegram_recovery_request_sync,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            email.strip().lower(),
            created_at.isoformat(timespec="seconds"),
            expires_at.isoformat(timespec="seconds"),
        )
        return token

    def _create_telegram_recovery_request_sync(
        self,
        token_hash: str,
        email: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM telegram_login_requests WHERE expires_at < ?",
                (created_at,),
            )
            identity = connection.execute(
                "SELECT auth_subject, user_id FROM auth_identities WHERE email = ?",
                (email,),
            ).fetchone()
            auth_subject = None
            user_id = None
            if identity is not None and int(identity["user_id"]) > 0:
                auth_subject = str(identity["auth_subject"])
                user_id = int(identity["user_id"])
            connection.execute(
                """
                INSERT INTO telegram_login_requests
                    (token_hash, status, user_id, request_type, auth_subject,
                     created_at, expires_at, approved_at)
                VALUES (?, 'pending', ?, 'recovery', ?, ?, ?, NULL)
                """,
                (token_hash, user_id, auth_subject, created_at, expires_at),
            )

    async def telegram_recovery_request_info(
        self,
        token: str,
        telegram_user_id: int,
        checked_at: datetime,
    ) -> dict[str, object] | None:
        if not token or len(token) > 128:
            return None
        return await asyncio.to_thread(
            self._telegram_recovery_request_info_sync,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            telegram_user_id,
            checked_at.isoformat(timespec="seconds"),
        )

    def _telegram_recovery_request_info_sync(
        self,
        token_hash: str,
        telegram_user_id: int,
        checked_at: str,
    ) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.status, r.user_id, r.expires_at, i.email
                FROM telegram_login_requests r
                LEFT JOIN auth_identities i ON i.auth_subject = r.auth_subject
                WHERE r.token_hash = ? AND r.request_type = 'recovery'
                  AND r.user_id = ? AND r.expires_at >= ?
                """,
                (token_hash, telegram_user_id, checked_at),
            ).fetchone()
        return dict(row) if row else None

    async def approve_telegram_recovery_request(
        self,
        token: str,
        telegram_user_id: int,
        approved_at: datetime,
    ) -> bool:
        if not token or len(token) > 128:
            return False
        return await asyncio.to_thread(
            self._approve_telegram_recovery_request_sync,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            telegram_user_id,
            approved_at.isoformat(timespec="seconds"),
        )

    def _approve_telegram_recovery_request_sync(
        self,
        token_hash: str,
        telegram_user_id: int,
        approved_at: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_login_requests
                SET status = 'approved', approved_at = ?
                WHERE token_hash = ? AND request_type = 'recovery'
                  AND user_id = ? AND status = 'pending' AND expires_at >= ?
                """,
                (approved_at, token_hash, telegram_user_id, approved_at),
            )
            return cursor.rowcount == 1

    async def telegram_recovery_status(
        self,
        token: str,
        checked_at: datetime,
    ) -> dict[str, object] | None:
        if not token or len(token) > 128:
            return None
        return await asyncio.to_thread(
            self._telegram_recovery_status_sync,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            checked_at.isoformat(timespec="seconds"),
        )

    def _telegram_recovery_status_sync(
        self,
        token_hash: str,
        checked_at: str,
    ) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, user_id, expires_at
                FROM telegram_login_requests
                WHERE token_hash = ? AND request_type = 'recovery'
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        if str(row["expires_at"]) < checked_at:
            return {"status": "expired", "user_id": None}
        return {
            "status": str(row["status"]),
            "user_id": int(row["user_id"]) if row["user_id"] is not None else None,
        }

    async def create_telegram_link_request(
        self,
        auth_subject: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> str:
        if not auth_subject or len(auth_subject) > 255:
            raise ValueError("Invalid auth subject")
        token = secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        await asyncio.to_thread(
            self._create_telegram_link_request_sync,
            token_hash,
            auth_subject,
            created_at.isoformat(timespec="seconds"),
            expires_at.isoformat(timespec="seconds"),
        )
        return token

    def _create_telegram_link_request_sync(
        self,
        token_hash: str,
        auth_subject: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM telegram_login_requests WHERE expires_at < ?",
                (created_at,),
            )
            connection.execute(
                """
                DELETE FROM telegram_login_requests
                WHERE request_type = 'link' AND auth_subject = ? AND status = 'pending'
                """,
                (auth_subject,),
            )
            connection.execute(
                """
                INSERT INTO telegram_login_requests
                    (token_hash, status, user_id, request_type, auth_subject,
                     created_at, expires_at, approved_at)
                VALUES (?, 'pending', NULL, 'link', ?, ?, ?, NULL)
                """,
                (token_hash, auth_subject, created_at, expires_at),
            )

    async def telegram_link_request_status(
        self,
        token: str,
        auth_subject: str,
        checked_at: datetime,
    ) -> dict[str, object] | None:
        if not token or len(token) > 128 or not auth_subject:
            return None
        return await asyncio.to_thread(
            self._telegram_link_request_status_sync,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            auth_subject,
            checked_at.isoformat(timespec="seconds"),
        )

    def _telegram_link_request_status_sync(
        self,
        token_hash: str,
        auth_subject: str,
        checked_at: str,
    ) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, user_id, expires_at
                FROM telegram_login_requests
                WHERE token_hash = ? AND request_type = 'link' AND auth_subject = ?
                """,
                (token_hash, auth_subject),
            ).fetchone()
        if row is None:
            return None
        if str(row["expires_at"]) < checked_at and str(row["status"]) == "pending":
            return {"status": "expired", "user_id": None}
        return {
            "status": str(row["status"]),
            "user_id": int(row["user_id"]) if row["user_id"] is not None else None,
        }

    async def confirm_telegram_link_request(
        self,
        token: str,
        telegram_user_id: int,
        approved_at: datetime,
    ) -> str:
        if not token or len(token) > 128 or telegram_user_id <= 0:
            return "invalid"
        return await asyncio.to_thread(
            self._confirm_telegram_link_request_sync,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            telegram_user_id,
            approved_at.isoformat(timespec="seconds"),
        )

    def _unique_account_name_sync(
        self,
        connection: _Connection,
        user_id: int,
        original: str,
    ) -> str:
        base = f"{original} (сайт)"
        candidate = base
        suffix = 2
        while connection.execute(
            "SELECT 1 FROM accounts WHERE user_id = ? AND name = ?",
            (user_id, candidate),
        ).fetchone() is not None:
            candidate = f"{base} {suffix}"
            suffix += 1
        return candidate

    def _move_user_data_sync(
        self,
        connection: _Connection,
        source_user_id: int,
        target_user_id: int,
    ) -> None:
        if source_user_id == target_user_id:
            return
        source_accounts = connection.execute(
            "SELECT id, name FROM accounts WHERE user_id = ? ORDER BY id",
            (source_user_id,),
        ).fetchall()
        for account in source_accounts:
            name = str(account["name"])
            if connection.execute(
                "SELECT 1 FROM accounts WHERE user_id = ? AND name = ?",
                (target_user_id, name),
            ).fetchone() is not None:
                name = self._unique_account_name_sync(connection, target_user_id, name)
                connection.execute(
                    "UPDATE accounts SET name = ? WHERE id = ?",
                    (name, int(account["id"])),
                )
        connection.execute(
            "UPDATE accounts SET user_id = ? WHERE user_id = ?",
            (target_user_id, source_user_id),
        )
        connection.execute(
            "UPDATE transactions SET user_id = ? WHERE user_id = ?",
            (target_user_id, source_user_id),
        )
        connection.execute(
            "UPDATE account_transfers SET user_id = ? WHERE user_id = ?",
            (target_user_id, source_user_id),
        )
        duplicate_categories = connection.execute(
            """
            SELECT source.id
            FROM custom_categories source
            WHERE source.user_id = ? AND EXISTS (
                SELECT 1 FROM custom_categories target
                WHERE target.user_id = ? AND target.kind = source.kind AND target.name = source.name
            )
            """,
            (source_user_id, target_user_id),
        ).fetchall()
        for row in duplicate_categories:
            connection.execute(
                "DELETE FROM custom_categories WHERE id = ?",
                (int(row["id"]),),
            )
        connection.execute(
            "UPDATE custom_categories SET user_id = ? WHERE user_id = ?",
            (target_user_id, source_user_id),
        )
        source_shortcut = connection.execute(
            "SELECT token_hash, created_at FROM shortcut_access WHERE user_id = ?",
            (source_user_id,),
        ).fetchone()
        target_shortcut = connection.execute(
            "SELECT 1 FROM shortcut_access WHERE user_id = ?",
            (target_user_id,),
        ).fetchone()
        if source_shortcut is not None and target_shortcut is not None:
            connection.execute(
                """
                INSERT INTO shortcut_access_history (token_hash, user_id, created_at)
                VALUES (?, ?, ?) ON CONFLICT(token_hash) DO NOTHING
                """,
                (source_shortcut["token_hash"], target_user_id, source_shortcut["created_at"]),
            )
            connection.execute(
                "DELETE FROM shortcut_access WHERE user_id = ?",
                (source_user_id,),
            )
        else:
            connection.execute(
                "UPDATE shortcut_access SET user_id = ? WHERE user_id = ?",
                (target_user_id, source_user_id),
            )
        connection.execute(
            "UPDATE shortcut_access_history SET user_id = ? WHERE user_id = ?",
            (target_user_id, source_user_id),
        )

    def _confirm_telegram_link_request_sync(
        self,
        token_hash: str,
        telegram_user_id: int,
        approved_at: str,
    ) -> str:
        try:
            with self._connect() as connection:
                request = connection.execute(
                    """
                    SELECT auth_subject FROM telegram_login_requests
                    WHERE token_hash = ? AND request_type = 'link'
                      AND status = 'pending' AND expires_at >= ?
                    """,
                    (token_hash, approved_at),
                ).fetchone()
                if request is None:
                    return "expired"
                auth_subject = str(request["auth_subject"] or "")
                identity = connection.execute(
                    "SELECT user_id FROM auth_identities WHERE auth_subject = ?",
                    (auth_subject,),
                ).fetchone()
                if identity is None:
                    return "invalid"
                occupied = connection.execute(
                    "SELECT auth_subject FROM auth_identities WHERE user_id = ?",
                    (telegram_user_id,),
                ).fetchone()
                if occupied is not None and str(occupied["auth_subject"]) != auth_subject:
                    return "already_linked"
                source_user_id = int(identity["user_id"])
                self._move_user_data_sync(connection, source_user_id, telegram_user_id)
                connection.execute(
                    "UPDATE auth_identities SET user_id = ? WHERE auth_subject = ?",
                    (telegram_user_id, auth_subject),
                )
                connection.execute(
                    """
                    UPDATE telegram_login_requests
                    SET status = 'approved', user_id = ?, approved_at = ?
                    WHERE token_hash = ?
                    """,
                    (telegram_user_id, approved_at, token_hash),
                )
                return "approved"
        except (sqlite3.IntegrityError, PostgresIntegrityError):
            return "already_linked"

    async def telegram_link_for_auth(self, auth_subject: str) -> dict[str, object] | None:
        return await asyncio.to_thread(self._telegram_link_for_auth_sync, auth_subject)

    def _telegram_link_for_auth_sync(self, auth_subject: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.user_id, p.display_name, p.username
                FROM auth_identities a
                LEFT JOIN user_profiles p ON p.user_id = a.user_id
                WHERE a.auth_subject = ?
                """,
                (auth_subject,),
            ).fetchone()
        if row is None or int(row["user_id"]) <= 0:
            return None
        return {
            "user_id": int(row["user_id"]),
            "display_name": row["display_name"],
            "username": row["username"],
        }

    async def unlink_telegram_auth(self, auth_subject: str) -> int | None:
        return await asyncio.to_thread(self._unlink_telegram_auth_sync, auth_subject)

    def _unlink_telegram_auth_sync(self, auth_subject: str) -> int | None:
        with self._connect() as connection:
            identity = connection.execute(
                "SELECT user_id FROM auth_identities WHERE auth_subject = ?",
                (auth_subject,),
            ).fetchone()
            if identity is None or int(identity["user_id"]) <= 0:
                return None
            old_user_id = int(identity["user_id"])
            while True:
                new_user_id = -(secrets.randbits(62) + 1)
                occupied = connection.execute(
                    "SELECT 1 FROM auth_identities WHERE user_id = ?",
                    (new_user_id,),
                ).fetchone()
                if occupied is None:
                    break
            self._move_user_data_sync(connection, old_user_id, new_user_id)
            connection.execute(
                "UPDATE auth_identities SET user_id = ? WHERE auth_subject = ?",
                (new_user_id, auth_subject),
            )
            return new_user_id

    async def upsert_user_profile(
        self,
        user_id: int,
        display_name: str | None,
        username: str | None,
        created_at: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_user_profile_sync,
            user_id,
            display_name,
            username,
            created_at.isoformat(timespec="seconds"),
        )

    def _upsert_user_profile_sync(
        self,
        user_id: int,
        display_name: str | None,
        username: str | None,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_profiles
                    (user_id, display_name, username, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (user_id, display_name, username, created_at, created_at),
            )

    async def bind_auth_identity(
        self,
        auth_subject: str,
        user_id: int,
        created_at: datetime,
    ) -> bool:
        if not auth_subject or len(auth_subject) > 255:
            return False
        return await asyncio.to_thread(
            self._bind_auth_identity_sync,
            auth_subject,
            user_id,
            created_at.isoformat(timespec="seconds"),
        )

    def _bind_auth_identity_sync(
        self,
        auth_subject: str,
        user_id: int,
        created_at: str,
    ) -> bool:
        try:
            with self._connect() as connection:
                current = connection.execute(
                    "SELECT user_id FROM auth_identities WHERE auth_subject = ?",
                    (auth_subject,),
                ).fetchone()
                if current is not None:
                    return int(current["user_id"]) == user_id
                connection.execute(
                    """
                    INSERT INTO auth_identities (auth_subject, user_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (auth_subject, user_id, created_at),
                )
                return True
        except (sqlite3.IntegrityError, PostgresIntegrityError):
            return False

    async def user_by_auth_subject(
        self,
        auth_subject: str,
        created_at: datetime,
        email: str | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._user_by_auth_subject_sync,
            auth_subject,
            created_at.isoformat(timespec="seconds"),
            email.strip().lower() if email else None,
        )

    def _user_by_auth_subject_sync(
        self,
        auth_subject: str,
        created_at: str,
        email: str | None,
    ) -> int:
        digest = hashlib.sha256(auth_subject.encode("utf-8")).digest()
        generated_user_id = -(int.from_bytes(digest[:7], "big") + 1)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM auth_identities WHERE auth_subject = ?",
                (auth_subject,),
            ).fetchone()
            if row is not None:
                if email:
                    connection.execute(
                        "UPDATE auth_identities SET email = ? WHERE auth_subject = ?",
                        (email, auth_subject),
                    )
                return int(row["user_id"])
            connection.execute(
                """
                INSERT INTO auth_identities (auth_subject, user_id, created_at, email)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(auth_subject) DO NOTHING
                """,
                (auth_subject, generated_user_id, created_at, email),
            )
            row = connection.execute(
                "SELECT user_id FROM auth_identities WHERE auth_subject = ?",
                (auth_subject,),
            ).fetchone()
        assert row is not None
        return int(row["user_id"])

    async def auth_identity_by_email(self, email: str) -> dict[str, object] | None:
        return await asyncio.to_thread(
            self._auth_identity_by_email_sync,
            email.strip().lower(),
        )

    def _auth_identity_by_email_sync(self, email: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT auth_subject, user_id, email FROM auth_identities WHERE email = ?",
                (email,),
            ).fetchone()
        return dict(row) if row else None

    async def users_overview(self) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._users_overview_sync)

    def _users_overview_sync(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH user_ids AS (
                    SELECT user_id FROM accounts
                    UNION SELECT user_id FROM transactions
                    UNION SELECT user_id FROM custom_categories
                    UNION SELECT user_id FROM shortcut_access
                    UNION SELECT user_id FROM user_profiles
                    UNION SELECT user_id FROM auth_identities
                )
                SELECT u.user_id, p.display_name, p.username,
                       (SELECT email FROM auth_identities i WHERE i.user_id = u.user_id) AS email,
                       (SELECT COUNT(*) FROM accounts a WHERE a.user_id = u.user_id) AS account_count,
                       (SELECT COUNT(*) FROM transactions t WHERE t.user_id = u.user_id) AS transaction_count
                FROM user_ids u
                LEFT JOIN user_profiles p ON p.user_id = u.user_id
                ORDER BY transaction_count DESC, account_count DESC, u.user_id
                """
            ).fetchall()
        return [dict(row) for row in rows]
