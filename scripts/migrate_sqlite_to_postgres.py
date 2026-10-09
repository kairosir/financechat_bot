from __future__ import annotations

import os
import sqlite3
import sys

import psycopg


TABLES = {
    "accounts": (
        "id, user_id, name, balance_minor, created_at, account_type, currency, "
        "interest_rate_bps, effective_interest_rate_bps, daily_interest_enabled",
        "id",
    ),
    "transactions": (
        "id, user_id, kind, category, amount_minor, note, created_at, account_id, currency",
        "id",
    ),
    "custom_categories": (
        "id, user_id, kind, name, created_at",
        "id",
    ),
    "account_transfers": (
        "id, user_id, from_account_id, to_account_id, amount_minor, note, created_at",
        "id",
    ),
    "shortcut_access": (
        "user_id, token_hash, created_at",
        "user_id",
    ),
    "shortcut_access_history": (
        "token_hash, user_id, created_at",
        "token_hash",
    ),
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: migrate_sqlite_to_postgres.py SOURCE_DB")
    database_url = os.environ["DATABASE_URL"]
    source = sqlite3.connect(sys.argv[1])
    source.row_factory = sqlite3.Row
    with psycopg.connect(database_url) as target:
        for table, (columns_text, conflict_key) in TABLES.items():
            exists = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            columns = [item.strip() for item in columns_text.split(",")]
            rows = source.execute(
                f"SELECT {columns_text} FROM {table}"
            ).fetchall()
            if not rows:
                continue
            placeholders = ", ".join(["%s"] * len(columns))
            updates = ", ".join(
                f"{column} = EXCLUDED.{column}"
                for column in columns
                if column != conflict_key
            )
            with target.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {table} ({columns_text}) VALUES ({placeholders})
                    ON CONFLICT ({conflict_key}) DO UPDATE SET {updates}
                    """,
                    [tuple(row[column] for column in columns) for row in rows],
                )
            print(f"{table}: {len(rows)}")

        for table in ("accounts", "transactions", "custom_categories", "account_transfers"):
            target.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                "GREATEST(COALESCE((SELECT MAX(id) FROM " + table + "), 0), 1), "
                "COALESCE((SELECT MAX(id) FROM " + table + "), 0) > 0)",
                (table,),
            )
    source.close()


if __name__ == "__main__":
    main()
