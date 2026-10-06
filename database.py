from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class AccountTransfer:
    id: int
    user_id: int
    from_account_id: int
    to_account_id: int
    amount_minor: int
    note: str | None
    created_at: str


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
                       account_type, currency, interest_rate_bps
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
                       account_type, currency, interest_rate_bps
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
                       account_type, currency, interest_rate_bps
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
    ) -> Account | None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts
                        (user_id, name, balance_minor, created_at, account_type, currency, interest_rate_bps)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """ + (" RETURNING id" if self.postgres else "") + """
                    """,
                    (user_id, name, balance_minor, created_at, account_type, currency, interest_rate_bps),
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
                SELECT kind, amount_minor, account_id FROM transactions
                WHERE id = ? AND user_id = ?
                """,
                (transaction_id, user_id),
            ).fetchone()
            if item is None:
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
