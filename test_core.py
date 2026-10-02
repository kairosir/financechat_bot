import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot import format_money, parse_amount
from database import Database


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


if __name__ == "__main__":
    unittest.main()

