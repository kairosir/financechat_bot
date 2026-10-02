from __future__ import annotations

import asyncio
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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_user_created
                ON transactions(user_id, created_at DESC)
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
    ) -> int:
        return await asyncio.to_thread(
            self._add_transaction_sync,
            user_id,
            kind,
            category,
            amount_minor,
            note,
            created_at.isoformat(timespec="seconds"),
        )

    def _add_transaction_sync(
        self,
        user_id: int,
        kind: str,
        category: str,
        amount_minor: int,
        note: str | None,
        created_at: str,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transactions
                    (user_id, kind, category, amount_minor, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, kind, category, amount_minor, note, created_at),
            )
            return int(cursor.lastrowid)

    async def recent(self, user_id: int, limit: int = 10) -> list[Transaction]:
        return await asyncio.to_thread(self._recent_sync, user_id, limit)

    def _recent_sync(self, user_id: int, limit: int) -> list[Transaction]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, kind, category, amount_minor, note, created_at
                FROM transactions
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [Transaction(**dict(row)) for row in rows]

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
            cursor = connection.execute(
                "DELETE FROM transactions WHERE id = ? AND user_id = ?",
                (transaction_id, user_id),
            )
            return cursor.rowcount == 1
