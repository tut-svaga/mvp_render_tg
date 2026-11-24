import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "stage_d_cache_watcher.py"
SPEC = importlib.util.spec_from_file_location("stage_d_cache_watcher", MODULE_PATH)
watcher = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(watcher)


class CacheWatcherTests(unittest.TestCase):
    def write_cache(self, directory: str, missing_actual: str | None = None,
                    missing_forecast: str | None = None,
                    missing_previous: str | None = None) -> Path:
        path = Path(directory) / "cache.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["date", "event", "actual", "forecast", "previous"])
            for name in watcher.REPORTS:
                writer.writerow([
                    "2026-11-25",
                    name,
                    "" if name == missing_actual else "1.0%",
                    "" if name == missing_forecast else "0.9%",
                    "" if name == missing_previous else "0.8%",
                ])
        return path

    def test_seven_of_eight_actuals_must_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.write_cache(directory, missing_actual=watcher.REPORTS[-1])
            found, missing, _ = watcher.read_cache(cache, watcher.date(2026, 11, 25))
            self.assertEqual(len(found), 8)
            self.assertEqual(len(missing), 1)
            self.assertIn("Actual", missing[0])

    def test_eight_of_eight_actuals_are_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.write_cache(directory)
            found, missing, _ = watcher.read_cache(cache, watcher.date(2026, 11, 25))
            self.assertEqual(len(found), 8)
            self.assertEqual(missing, [])

    def test_release_readiness_also_requires_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.write_cache(directory, missing_previous=watcher.REPORTS[-1])
            found, missing, _ = watcher.read_cache(cache, watcher.date(2026, 11, 25))
            self.assertEqual(len(missing), 1)
            self.assertIn("Previous", missing[0])

    def test_pre_release_requires_forecast_and_previous_but_not_actual(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.write_cache(
                directory,
                missing_actual=watcher.REPORTS[0],
                missing_previous=watcher.REPORTS[1],
            )
            found, _, _ = watcher.read_cache(cache, watcher.date(2026, 11, 25))
            missing = watcher.pre_release_missing(found)
            self.assertEqual(len(missing), 1)
            self.assertIn("Previous", missing[0])

    def test_local_date_wins_over_previous_utc_date(self):
        row = [
            "2026-11-24T23:10:00+00:00",
            "USD", "High", "Core PCE Price Index m/m", "", "0.3%", "0.3%",
            "", "2026-11-25", "01:10:00",
            "2026-11-25T01:10:00+02:00", "2026-11-24T23:10:00+00:00",
        ]
        parsed = watcher.event_date_for_row(row, [0, 10, 11, 8])
        self.assertEqual(parsed, watcher.date(2026, 11, 25))


if __name__ == "__main__":
    unittest.main()
