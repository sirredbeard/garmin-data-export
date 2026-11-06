"""The store writes atomically and reads back what it wrote. A sync can
be killed at any point, so a half-written file is the failure mode
these tests exist to rule out."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from garmin_daily.store import Store


class StoreTests(unittest.TestCase):
    def test_day_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            day = date(2026, 11, 5)
            self.assertFalse(store.has_day(day))
            store.put_day(day, {"date": "2026-11-05", "summary": {"totalSteps": 1}})
            self.assertTrue(store.has_day(day))
            self.assertEqual(store.get_day(day)["summary"]["totalSteps"], 1)
            self.assertEqual(store.stored_days(), [day])
            leftovers = list(Path(tmp).rglob("*.tmp"))
            self.assertEqual(leftovers, [])

    def test_range_merge_keeps_old_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.put_range("weigh_ins", {"2026-11-01": [{"weight_lb": 176.0}]})
            store.put_range("weigh_ins", {"2026-11-02": [{"weight_lb": 175.0}]})
            merged = store.get_range("weigh_ins")
            self.assertEqual(sorted(merged), ["2026-11-01", "2026-11-02"])


if __name__ == "__main__":
    unittest.main()
