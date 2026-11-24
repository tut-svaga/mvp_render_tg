#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end Telegram smoke test using synthetic Actual values.

The production calendar cache is never modified. A separate test cache is
written under output_stage_d and passed through the real watcher and predictor.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "forex_factory_cache.csv"
TEST_CACHE = ROOT / "output_stage_d" / "test_forex_factory_cache.csv"
OUTPUT = ROOT / "output_stage_d"
RELEASE_DATE = "2026-11-25"

SYNTHETIC_ACTUALS = {
    "Core Durable Goods Orders m/m": "0.6%",
    "Core PCE Price Index m/m": "0.4%",
    "Durable Goods Orders m/m": "0.9%",
    "Personal Income m/m": "0.3%",
    "Personal Spending m/m": "0.5%",
    "Prelim GDP Price Index q/q": "2.4%",
    "Prelim GDP q/q": "3.6%",
    "Unemployment Claims": "199K",
}


def main() -> int:
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID_SER"):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID_SER in GitHub Actions secrets.")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        fieldnames = reader.fieldnames
        rows = list(reader)

    found = set()
    for row in rows:
        event = (row.get("Event") or row.get("event") or "").strip()
        event_date = (row.get("datetime_utc") or row.get("DateTime") or row.get("datetime_local") or "")[:10]
        if event_date == RELEASE_DATE and event in SYNTHETIC_ACTUALS:
            value = SYNTHETIC_ACTUALS[event]
            row["Actual"] = value
            row["actual"] = value
            found.add(event)

    missing = sorted(set(SYNTHETIC_ACTUALS) - found)
    if missing:
        raise RuntimeError("Test cache is missing target reports: " + ", ".join(missing))

    with TEST_CACHE.open("w", encoding="utf-8", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    actuals_json = OUTPUT / "stage_d_actuals_test.json"
    subprocess.run([
        sys.executable, str(ROOT / "stage_d_cache_watcher.py"),
        "--cache", str(TEST_CACHE), "--date", RELEASE_DATE,
        "--output-json", str(actuals_json), "--once",
    ], cwd=ROOT, check=True)

    subprocess.run([
        sys.executable, str(ROOT / "stage_d_predict_existing.py"),
        "--model", str(ROOT / "stage_d_model.json"),
        "--input-json", str(actuals_json),
        "--output-dir", str(OUTPUT),
    ], cwd=ROOT, check=True)

    message_path = OUTPUT / "stage_d_telegram_message.html"
    message = message_path.read_text(encoding="utf-8")
    message_path.write_text(
        "<b>TEST — синтетические Actual, не реальные данные</b>\n"
        "<b>Публикация для теста: 25.11.2026 15:30 (Europe/Chisinau)</b>\n\n"
        + message,
        encoding="utf-8",
    )

    subprocess.run([sys.executable, str(ROOT / "telegram_mvp.py")], cwd=ROOT, check=True)
    print("Synthetic pipeline completed; Telegram send request succeeded.")
    print("Production cache was not modified:", SOURCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
