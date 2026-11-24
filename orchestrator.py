#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the eight-report XAUUSD release pipeline.

The scraper is executed from its own directory so its existing relative paths
remain unchanged. Paths can be overridden from the command line.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("release_orchestrator")


def wait_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(target.tzinfo)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 10))


def run_update(update_script: Path, scraper_dir: Path) -> None:
    if not update_script.is_file():
        raise FileNotFoundError(
            f"Forex Factory updater not found: {update_script}. "
            "Check the scraper submodule checkout and --update-script."
        )
    LOG.info("Updating Forex Factory cache: %s (cwd=%s)", update_script, scraper_dir)
    result = subprocess.run([sys.executable, str(update_script)], cwd=scraper_dir)
    if result.returncode:
        raise RuntimeError(f"Forex Factory update.py exited with code {result.returncode}")


def check_actuals(watcher: Path, cache: Path, release_date: str, output_json: Path) -> bool:
    command = [
        sys.executable, str(watcher),
        "--cache", str(cache),
        "--date", release_date,
        "--output-json", str(output_json),
        "--once",
    ]
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    raise RuntimeError(f"stage_d_cache_watcher.py failed with code {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchestrate the eight-report XAUUSD release.")
    parser.add_argument("--release-date", required=True, help="Release date in YYYY-MM-DD")
    parser.add_argument("--release-time", default="15:30", help="Release time in the selected timezone (default: 15:30)")
    parser.add_argument("--timezone", default="Europe/Chisinau", help="IANA timezone (default: Europe/Chisinau)")
    parser.add_argument("--scraper-dir", type=Path, default=ROOT / "forexfactory-scraper")
    parser.add_argument("--update-script", type=Path, default=ROOT / "forexfactory-scraper" / "update.py")
    parser.add_argument("--cache", type=Path, default=ROOT / "forexfactory-scraper" / "data" / "economic_data" / "forex_factory_cache.csv")
    parser.add_argument("--model", type=Path, default=ROOT / "stage_d_model.json")
    parser.add_argument("--watcher", type=Path, default=ROOT / "stage_d_cache_watcher.py")
    parser.add_argument("--predictor", type=Path, default=ROOT / "stage_d_predict_existing.py")
    parser.add_argument("--telegram-script", type=Path, default=ROOT / "telegram_mvp.py")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output_stage_d")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-wait-minutes", type=int, default=75)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    try:
        tz = ZoneInfo(args.timezone)
        release = datetime.strptime(
            f"{args.release_date} {args.release_time}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=tz)
    except Exception as exc:
        parser.error(f"Invalid release date/time/timezone: {exc}")

    if args.poll_seconds < 1 or args.max_wait_minutes < 1:
        parser.error("--poll-seconds and --max-wait-minutes must be positive")

    def absolute(path: Path) -> Path:
        return path if path.is_absolute() else (ROOT / path).resolve()

    scraper_dir = absolute(args.scraper_dir)
    update_script = absolute(args.update_script)
    cache = absolute(args.cache)
    model = absolute(args.model)
    watcher = absolute(args.watcher)
    predictor = absolute(args.predictor)
    telegram_script = absolute(args.telegram_script)
    output_dir = absolute(args.output_dir)
    actuals_json = output_dir / "stage_d_actuals.json"

    # Preserve the scraper's own working directory and relative path behavior.
    if not scraper_dir.is_dir():
        raise FileNotFoundError(
            f"Scraper directory not found: {scraper_dir}. "
            "The forexfactory-scraper submodule must be checked out."
        )

    checkpoints = [
        ("T-15 minutes", release - timedelta(minutes=15)),
        ("T-1 minute", release - timedelta(minutes=1)),
        ("release time", release),
    ]
    now = datetime.now(tz)
    for label, checkpoint in checkpoints:
        if now <= checkpoint:
            wait_until(checkpoint)
        LOG.info("Checkpoint %s (%s)", label, checkpoint.strftime("%Y-%m-%d %H:%M %Z"))
        run_update(update_script, scraper_dir)
        now = datetime.now(tz)

    deadline = release + timedelta(minutes=args.max_wait_minutes)
    attempt = 0
    while datetime.now(tz) <= deadline:
        attempt += 1
        LOG.info("Post-release update/check #%d", attempt)
        run_update(update_script, scraper_dir)
        if check_actuals(watcher, cache, args.release_date, actuals_json):
            LOG.info("All eight Actual values are present. Starting prediction pipeline.")
            break
        if datetime.now(tz) >= deadline:
            raise TimeoutError(
                f"Not all eight Actual values appeared within {args.max_wait_minutes} minutes."
            )
        wait_until(min(datetime.now(tz) + timedelta(seconds=args.poll_seconds), deadline))
    else:
        raise TimeoutError("Release polling deadline reached.")

    if not model.is_file():
        raise FileNotFoundError(f"Stage D model not found: {model}")

    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, str(predictor),
        "--model", str(model),
        "--input-json", str(actuals_json),
        "--output-dir", str(output_dir),
    ], cwd=ROOT, check=True)

    # Add the scheduled release label to the existing model-generated HTML.
    message_path = output_dir / "stage_d_telegram_message.html"
    message = message_path.read_text(encoding="utf-8")
    label = release.strftime("%d.%m.%Y %H:%M %Z")
    message_path.write_text(
        f"<b>Публикация: {label}</b>\n\n" + message,
        encoding="utf-8",
    )

    subprocess.run([sys.executable, str(telegram_script)], cwd=ROOT, check=True)
    LOG.info("Release pipeline completed and Telegram message sent.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
        LOG.exception("Release orchestration failed.")
        raise SystemExit(1)
