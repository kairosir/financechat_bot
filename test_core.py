import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot import format_money, parse_amount
from database import Database
from aiohttp.test_utils import TestClient, TestServer
from shortcut_api import (
    clean_note,
    create_app,
    parse_amount as parse_shortcut_amount,
    parse_balance as parse_shortcut_balance,
)


class AmountTests(unittest.TestCase):
    def test_parse_amount(self) -> None:
        self.assertEqual(parse_amount("1 250,50"), 125050)
        self.assertEqual(parse_amount("99.999"), 10000)
        self.assertIsNone(parse_amount("abc"))
        self.assertIsNone(parse_amount("-5"))
        self.assertIsNone(parse_amount("NaN"))

    def test_format_money(self) -> None:
        self.assertEqual(format_money(125000), "1 250 ₸")
        self.assertEqual(format_money(125050), "1 250.50 ₸")

    def test_shortcut_input(self) -> None:
        self.assertEqual(parse_shortcut_amount("12 500,75"), 1250075)
        self.assertIsNone(parse_shortcut_amount("0"))
        self.assertEqual(parse_shortcut_balance("-1500"), -150000)
        self.assertEqual(clean_note("  кофе   с другом "), "кофе с другом")
        self.assertIsNone(clean_note("  "))
        with self.assertRaises(ValueError):
            clean_note("x" * 121)


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.temp_dir.name) / "test.db"))
        await self.database.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_add_totals_and_delete(self) -> None:
        now = datetime(2026, 10, 2, 12, 30, tzinfo=ZoneInfo("Asia/Almaty"))
        expense_id = await self.database.add_transaction(
            42, "expense", "food", 250000, "Обед", now
        )
        await self.database.add_transaction(
            42, "income", "salary", 5000000, None, now
        )

        income, expense, categories = await self.database.totals(42)
        self.assertEqual((income, expense), (5000000, 250000))
        self.assertEqual(categories, [("food", 250000)])
        self.assertEqual(len(await self.database.recent(42)), 2)

        self.assertFalse(await self.database.delete_owned(expense_id, 7))
        self.assertTrue(await self.database.delete_owned(expense_id, 42))

    async def test_existing_database_migrates_without_data_loss(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transactions
                    (user_id, kind, category, amount_minor, note, created_at)
                VALUES (42, 'expense', 'food', 10000, NULL, '2026-10-01T10:00:00+05:00')
                """
            )
        migrated = Database(str(legacy_path))
        await migrated.initialize()
        items = await migrated.recent(42)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].amount_minor, 10000)
        self.assertIsNone(items[0].account_id)

    async def test_custom_categories_and_history_filter(self) -> None:
        now = datetime(2026, 10, 2, 12, 30, tzinfo=ZoneInfo("Asia/Almaty"))
        category = await self.database.add_custom_category(
            42, "expense", "☕ Кофе", now
        )
        self.assertIsNotNone(category)
        self.assertIsNone(
            await self.database.add_custom_category(42, "expense", "☕ Кофе", now)
        )
        self.assertEqual(
            [item.name for item in await self.database.custom_categories(42, "expense")],
            ["☕ Кофе"],
        )

        await self.database.add_transaction(
            42, "expense", "custom|☕ Кофе", 180000, None, now
        )
        await self.database.add_transaction(
            42, "income", "salary", 5000000, None, now
        )
        expenses = await self.database.recent(42, kind="expense")
        self.assertEqual(len(expenses), 1)
        self.assertEqual(expenses[0].category, "custom|☕ Кофе")

        assert category is not None
        self.assertFalse(await self.database.delete_custom_category(category.id, 7))
        self.assertTrue(await self.database.delete_custom_category(category.id, 42))
        self.assertEqual(len(await self.database.recent(42, kind="expense")), 1)

    async def test_shortcut_token_rotation(self) -> None:
        now = datetime(2026, 10, 3, 12, 30, tzinfo=ZoneInfo("Asia/Almaty"))
        first = await self.database.rotate_shortcut_token(42, now)
        self.assertEqual(await self.database.user_by_shortcut_token(first), 42)
        second = await self.database.rotate_shortcut_token(42, now)
        self.assertNotEqual(first, second)
        self.assertIsNone(await self.database.user_by_shortcut_token(first))
        self.assertEqual(await self.database.user_by_shortcut_token(second), 42)

    async def test_accounts_balances_transfer_and_undo(self) -> None:
        now = datetime(2026, 10, 3, 12, 30, tzinfo=ZoneInfo("Asia/Almaty"))
        cash = await self.database.add_account(42, "Наличные", 100000, now)
        card = await self.database.add_account(42, "Kaspi", 250000, now)
        assert cash is not None and card is not None

        transaction_id = await self.database.add_transaction(
            42, "expense", "food", 25000, "Обед", now, account_id=cash.id
        )
        updated_cash = await self.database.get_account(cash.id, 42)
        assert updated_cash is not None
        self.assertEqual(updated_cash.balance_minor, 75000)

        await self.database.transfer(42, card.id, cash.id, 50000, None, now)
        updated_cash = await self.database.get_account(cash.id, 42)
        updated_card = await self.database.get_account(card.id, 42)
        assert updated_cash is not None and updated_card is not None
        self.assertEqual(updated_cash.balance_minor, 125000)
        self.assertEqual(updated_card.balance_minor, 200000)

        self.assertTrue(await self.database.delete_owned(transaction_id, 42))
        updated_cash = await self.database.get_account(cash.id, 42)
        assert updated_cash is not None
        self.assertEqual(updated_cash.balance_minor, 150000)

    async def test_shortcut_api_writes_to_shared_database(self) -> None:
        timezone = ZoneInfo("Asia/Almaty")
        token = await self.database.rotate_shortcut_token(
            42, datetime(2026, 10, 3, 12, 30, tzinfo=timezone)
        )
        headers = {"Authorization": f"Bearer {token}"}
        async with TestClient(TestServer(create_app(self.database, timezone))) as client:
            page = await client.get(f"/iphone?t={token}")
            self.assertEqual(page.status, 200)

            categories = await client.get(
                "/api/v1/categories?kind=expense", headers=headers
            )
            self.assertEqual(categories.status, 200)
            category_data = await categories.json()
            self.assertIn(
                {"value": "food", "label": "🍽 Еда"},
                category_data["categories"],
            )

            created_account = await client.post(
                "/api/v1/accounts",
                headers=headers,
                json={"name": "Kaspi", "balance": "100000"},
            )
            self.assertEqual(created_account.status, 200)
            account_data = await created_account.json()

            options_response = await client.get(
                "/api/v1/shortcut/options?kind=expense",
                headers=headers,
            )
            self.assertEqual(options_response.status, 200)
            options = await options_response.json()
            self.assertIn("🍽 Еда", options["category_labels"])
            kaspi_label = next(
                label for label in options["account_labels"] if label.startswith("Kaspi ·")
            )

            response = await client.post(
                "/api/v1/transactions",
                headers=headers,
                json={
                    "kind": "expense",
                    "category": "🍽 Еда",
                    "account": "Kaspi",
                    "amount": "1250,50",
                    "note": "Обед",
                },
            )
            self.assertEqual(response.status, 200)

            native_response = await client.get(
                "/api/v1/shortcut/add",
                params={
                    "t": token,
                    "kind": "Расход",
                    "category": "🍽 Еда",
                    "account": "Kaspi",
                    "amount": "100",
                    "note": "Команда iPhone",
                },
            )
            self.assertEqual(native_response.status, 200)

        items = await self.database.recent(42)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].amount_minor, 10000)
        self.assertEqual(items[0].note, "Команда iPhone")
        account = await self.database.get_account(account_data["id"], 42)
        assert account is not None
        self.assertEqual(account.balance_minor, 9864950)


if __name__ == "__main__":
    unittest.main()
