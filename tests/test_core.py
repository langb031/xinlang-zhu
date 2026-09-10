import tempfile
import unittest
from pathlib import Path

import pandas as pd

from fund_data import init_db, load_frame, save_bundle


class CacheTests(unittest.TestCase):
    def test_dataframe_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            frame = pd.DataFrame({"nav_date": ["2026-09-09"], "unit_nav": [2.0]})
            save_bundle(database, "050009", {"nav": (frame, "2026-09-09", "fixture")})
            actual = load_frame(database, "050009", "nav")
            self.assertEqual(actual.to_dict("records"), frame.to_dict("records"))

    def test_empty_refresh_cannot_replace_old_data(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            old = pd.DataFrame({"nav_date": ["2026-09-09"], "unit_nav": [2.0]})
            save_bundle(database, "050009", {"nav": (old, "2026-09-09", "fixture")})
            with self.assertRaisesRegex(ValueError, "空数据"):
                save_bundle(database, "050009", {"nav": (pd.DataFrame(), "2026-09-10", "fixture")})
            actual = load_frame(database, "050009", "nav")
            self.assertEqual(actual.iloc[0]["unit_nav"], 2.0)
