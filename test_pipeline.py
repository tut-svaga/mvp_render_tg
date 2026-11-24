#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end Telegram smoke test using an isolated synthetic cache fixture."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEST_CACHE = ROOT / "data" / "forex_factory_cache_test.csv"
OUTPUT = ROOT / "output_stage_d"
RELEASE_DATE = "2026-11-25"


def main() -> int:
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID_SER"):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID_SER in GitHub Actions secrets.")
    if not TEST_CACHE.is_file():
        raise FileNotFoundError(f"Synthetic test cache not found: {TEST_CACHE}")

    OUTPUT.mkdir(parents=True, exist_ok=True)
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
    print("The production calendar cache was not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
