from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


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
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> None:
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
            if account_id is not None:
                account = connection.execute(
                    "SELECT id FROM accounts WHERE id = ? AND user_id = ?",
                    (account_id, user_id),
                ).fetchone()
                if account is None:
                    raise ValueError("Счёт не найден")
                delta = amount_minor if kind == "income" else -amount_minor
                connection.execute(
                    "UPDATE accounts SET balance_minor = balance_minor + ? WHERE id = ?",
                    (delta, account_id),
                )
            cursor = connection.execute(
                """
                INSERT INTO transactions
                    (user_id, kind, category, amount_minor, note, created_at, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, kind, category, amount_minor, note, created_at, account_id),
            )
            return int(cursor.lastrowid)

    async def recent(
        self,
        user_id: int,
        limit: int = 10,
        kind: str | None = None,
    ) -> list[Transaction]:
        return await asyncio.to_thread(self._recent_sync, user_id, limit, kind)

    def _recent_sync(
        self,
        user_id: int,
        limit: int,
        kind: str | None,
    ) -> list[Transaction]:
        condition = "t.user_id = ?"
        params: list[object] = [user_id]
        if kind:
            condition += " AND t.kind = ?"
            params.append(kind)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT t.id, t.user_id, t.kind, t.category, t.amount_minor,
                       t.note, t.created_at, t.account_id, a.name AS account_name
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
                    """,
                    (user_id, kind, name, created_at),
                )
                item_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
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
                SELECT id, user_id, name, balance_minor, created_at
                FROM accounts WHERE user_id = ? ORDER BY id LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts (user_id, name, balance_minor, created_at)
                    VALUES (?, 'Основной', 0, ?)
                    """,
                    (user_id, created_at),
                )
                return Account(int(cursor.lastrowid), user_id, "Основной", 0, created_at)
        return Account(**dict(row))

    async def accounts(self, user_id: int) -> list[Account]:
        return await asyncio.to_thread(self._accounts_sync, user_id)

    def _accounts_sync(self, user_id: int) -> list[Account]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, name, balance_minor, created_at
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
                SELECT id, user_id, name, balance_minor, created_at
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
    ) -> Account | None:
        return await asyncio.to_thread(
            self._add_account_sync,
            user_id,
            name,
            balance_minor,
            created_at.isoformat(timespec="seconds"),
        )

    def _add_account_sync(
        self,
        user_id: int,
        name: str,
        balance_minor: int,
        created_at: str,
    ) -> Account | None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts (user_id, name, balance_minor, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, name, balance_minor, created_at),
                )
                account_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None
        return Account(account_id, user_id, name, balance_minor, created_at)

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
        except sqlite3.IntegrityError:
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
                "SELECT id FROM accounts WHERE user_id = ? AND id IN (?, ?)",
                (user_id, from_account_id, to_account_id),
            ).fetchall()
            if len(rows) != 2:
                raise ValueError("Счёт не найден")
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
            return int(cursor.lastrowid)

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
        return int(row["user_id"]) if row else None
