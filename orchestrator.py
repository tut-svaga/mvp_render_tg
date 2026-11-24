#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reliable eight-report XAUUSD release pipeline with isolated synthetic test mode."""
from __future__ import annotations

import argparse
import csv
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("release_orchestrator")

REPORTS = {
    "Core PCE Price Index m/m": "0.4%",
    "Prelim GDP q/q": "3.6%",
    "Prelim GDP Price Index q/q": "2.4%",
    "Core Durable Goods Orders m/m": "0.6%",
    "Durable Goods Orders m/m": "0.9%",
    "Personal Income m/m": "0.3%",
    "Personal Spending m/m": "0.5%",
    "Unemployment Claims": "199K",
}


def absolute(path: Path) -> Path:
    return path if path.is_absolute() else (ROOT / path).resolve()


def run_update(script: Path, scraper_dir: Path, cache: Path, timezone: str) -> None:
    if not script.is_file():
        raise FileNotFoundError(f"Forex Factory updater not found: {script}")
    command = [
        sys.executable, str(script), "--cache", str(cache),
        "--timezone", timezone,
    ]
    LOG.info("Refreshing Forex Factory calendar: %s", cache)
    result = subprocess.run(command, cwd=scraper_dir)
    if result.returncode:
        raise RuntimeError(f"Forex Factory update.py exited with code {result.returncode}")


def refresh_with_retry(script: Path, scraper_dir: Path, cache: Path,
                       timezone: str, attempts: int = 3) -> None:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            run_update(script, scraper_dir, cache, timezone)
            return
        except Exception as exc:
            last_error = exc
            LOG.exception("Forex Factory refresh failed (%d/%d).", attempt, attempts)
            if attempt < attempts:
                time.sleep(10)
    raise RuntimeError(f"Forex Factory refresh failed after {attempts} attempts: {last_error}")


def watcher_check(watcher: Path, cache: Path, release_date: str,
                  output_json: Path, *, pre_release: bool = False) -> bool:
    command = [
        sys.executable, str(watcher), "--cache", str(cache),
        "--date", release_date, "--output-json", str(output_json), "--once",
    ]
    if pre_release:
        command.append("--pre-release")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    raise RuntimeError(f"stage_d_cache_watcher.py failed with code {result.returncode}")


def wait_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(target.tzinfo)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 10))


def clear_actuals(cache: Path) -> None:
    """Clear Actual in a test-only CSV; never called for production cache."""
    with cache.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows:
        raise ValueError(f"Test cache is empty: {cache}")
    header = [value.strip().lower() for value in rows[0]]
    indices = [i for i, value in enumerate(header) if value in {"actual", "actualvalue"}]
    if not indices:
        raise ValueError("Test cache has no Actual column.")
    for row in rows[1:]:
        for index in indices:
            if index < len(row):
                row[index] = ""
    with cache.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)


def prepare_test_cache(template: Path, destination: Path, release_date: str,
                       release_time: str) -> None:
    """Copy the fixture, clear Actual, and move the eight test events to test time."""
    if not template.is_file():
        raise FileNotFoundError(f"Test cache template not found: {template}")
    with template.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows:
        raise ValueError("Test cache template is empty.")
    header = [value.strip().lower() for value in rows[0]]
    event_i = header.index("event") if "event" in header else None
    if event_i is None:
        raise ValueError("Test cache template has no Event column.")
    actual_indices = [i for i, value in enumerate(header) if value == "actual"]
    date_i = header.index("date") if "date" in header else None
    time_i = header.index("time") if "time" in header else None
    local_i = header.index("datetime_local") if "datetime_local" in header else None
    utc_i = header.index("datetime_utc") if "datetime_utc" in header else None
    legacy_dt_i = header.index("datetime") if "datetime" in header else 0

    wanted = set(REPORTS)
    matched = set()
    target_day = date.fromisoformat(release_date)
    target_clock = datetime.strptime(release_time, "%H:%M").time()
    tz = ZoneInfo("Europe/Chisinau")
    local_dt = datetime.combine(target_day, target_clock, tzinfo=tz)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))

    for row in rows[1:]:
        event = row[event_i].strip() if event_i < len(row) else ""
        if event not in wanted:
            continue
        matched.add(event)
        for index in actual_indices:
            if index < len(row):
                row[index] = ""
        if date_i is not None:
            row[date_i] = release_date
        if time_i is not None:
            row[time_i] = target_clock.strftime("%H:%M:%S")
        if local_i is not None:
            row[local_i] = local_dt.isoformat()
        if utc_i is not None:
            row[utc_i] = utc_dt.isoformat()
        if legacy_dt_i < len(row):
            row[legacy_dt_i] = local_dt.isoformat()

    missing = sorted(wanted - matched)
    if missing:
        raise RuntimeError("Test fixture is missing required reports: " + ", ".join(missing))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    LOG.info("Prepared isolated test cache for %s %s; Actual cleared for all 8 reports.",
             release_date, release_time)


def filter_test_fixture_rows(cache: Path, release_date: str, release_time: str) -> None:
    """Keep the synthetic target rows isolated from live same-day events."""
    with cache.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    header = [value.strip().lower() for value in rows[0]]
    event_i, date_i = header.index("event"), header.index("date")
    time_i = header.index("time") if "time" in header else None
    kept = [rows[0]]
    target_time = release_time[:5]
    for row in rows[1:]:
        if event_i < len(row) and date_i < len(row):
            event = row[event_i].strip()
            if event in REPORTS and row[date_i].strip() == release_date:
                row_time = row[time_i].strip()[:5] if time_i is not None and time_i < len(row) else ""
                if row_time != target_time:
                    continue
        kept.append(row)
    with cache.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(kept)


def inject_test_actuals(cache: Path, release_date: str, names: list[str]) -> None:
    """Synthetic release values are deliberately restricted to test cache."""
    with cache.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    header = [value.strip().lower() for value in rows[0]]
    event_i = header.index("event")
    date_i = header.index("date")
    actual_indices = [i for i, value in enumerate(header) if value == "actual"]
    wanted = set(names)
    injected = set()
    for row in rows[1:]:
        if event_i >= len(row) or date_i >= len(row):
            continue
        if row[event_i].strip() not in wanted or row[date_i].strip() != release_date:
            continue
        for index in actual_indices:
            if index < len(row):
                row[index] = REPORTS[row[event_i].strip()]
        injected.add(row[event_i].strip())
    if injected != wanted:
        raise RuntimeError("Could not inject test values for: " + ", ".join(sorted(wanted - injected)))
    with cache.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    LOG.warning("TEST ONLY: synthetic Actual populated for %d/8 report(s).", len(injected))


def run_prediction(predictor: Path, model: Path, input_json: Path,
                   output_dir: Path, telegram_script: Path,
                   release: datetime, test_mode: bool) -> None:
    if not input_json.is_file():
        raise FileNotFoundError(f"Validated Stage D input was not created: {input_json}")
    subprocess.run([
        sys.executable, str(predictor), "--model", str(model),
        "--input-json", str(input_json), "--output-dir", str(output_dir),
    ], cwd=ROOT, check=True)

    message_path = output_dir / "stage_d_telegram_message.html"
    if not message_path.is_file():
        raise FileNotFoundError("Prediction did not create the Telegram message.")
    message = message_path.read_text(encoding="utf-8")
    prefix = "<b>TEST — синтетические Actual, не реальные данные</b>\\n" if test_mode else ""
    label = release.strftime("%d.%m.%Y %H:%M %Z")
    message_path.write_text(prefix + f"<b>Публикация: {label}</b>\\n\\n" + message,
                            encoding="utf-8")
    subprocess.run([sys.executable, str(telegram_script)], cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Eight-report XAUUSD release orchestrator.")
    parser.add_argument("--mode", choices=["release", "test"], default="release")
    parser.add_argument("--release-date", required=True)
    parser.add_argument("--release-time", default="15:30")
    parser.add_argument("--timezone", default="Europe/Chisinau")
    parser.add_argument("--scraper-dir", type=Path, default=ROOT / "forexfactory-scraper")
    parser.add_argument("--update-script", type=Path, default=ROOT / "forexfactory-scraper" / "update.py")
    parser.add_argument("--cache", type=Path, default=ROOT / "data" / "forex_factory_cache.csv")
    parser.add_argument("--test-cache-template", type=Path, default=ROOT / "data" / "forex_factory_cache_test.csv")
    parser.add_argument("--test-cache", type=Path, default=ROOT / "data" / "forex_factory_cache_test_run.csv")
    parser.add_argument("--model", type=Path, default=ROOT / "stage_d_model.json")
    parser.add_argument("--watcher", type=Path, default=ROOT / "stage_d_cache_watcher.py")
    parser.add_argument("--predictor", type=Path, default=ROOT / "stage_d_predict_existing.py")
    parser.add_argument("--telegram-script", type=Path, default=ROOT / "telegram_mvp.py")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output_stage_d")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-wait-minutes", type=int, default=75)
    parser.add_argument("--test-checkpoint-seconds", type=int, default=10,
                        help="Only test mode: delay between simulated pre-release checkpoints.")
    parser.add_argument("--test-release-gap-seconds", type=int, default=5,
                        help="Only test mode: delay before simulated release checkpoint.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    tz = ZoneInfo(args.timezone)
    release = datetime.strptime(
        f"{args.release_date} {args.release_time}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=tz)

    scraper_dir = absolute(args.scraper_dir)
    update_script = absolute(args.update_script)
    production_cache = absolute(args.cache)
    model = absolute(args.model)
    watcher = absolute(args.watcher)
    predictor = absolute(args.predictor)
    telegram_script = absolute(args.telegram_script)
    output_dir = absolute(args.output_dir)
    if not scraper_dir.is_dir():
        raise FileNotFoundError(f"Scraper directory not found: {scraper_dir}")
    if not model.is_file():
        raise FileNotFoundError(f"Stage D model not found: {model}")
    if args.poll_seconds < 1 or args.max_wait_minutes < 1:
        parser.error("--poll-seconds and --max-wait-minutes must be positive")

    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID_SER"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID_SER must be configured before starting.")

    test_mode = args.mode == "test"
    cache = absolute(args.test_cache) if test_mode else production_cache
    input_json = output_dir / ("stage_d_actuals_test.json" if test_mode else "stage_d_actuals.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Prevent stale output from a previous run being mistaken for this release.
    for stale in (input_json, output_dir / "stage_d_prediction.json",
                  output_dir / "stage_d_telegram_message.html"):
        if stale.exists():
            stale.unlink()

    if test_mode:
        if args.test_checkpoint_seconds < 0 or args.test_release_gap_seconds < 0:
            parser.error("Test checkpoint delays cannot be negative")
        prepare_test_cache(absolute(args.test_cache_template), cache,
                           args.release_date, args.release_time)
        LOG.warning("TEST MODE: production cache is not used or modified.")
        # Same updater, same CSV format and same cache path contract as release mode.
        refresh_with_retry(update_script, scraper_dir, cache, args.timezone)
        filter_test_fixture_rows(cache, args.release_date, args.release_time)
        clear_actuals(cache)
        if not watcher_check(watcher, cache, args.release_date, input_json, pre_release=True):
            raise RuntimeError("Test calendar fixture failed pre-release Forecast/Previous validation.")
        time.sleep(args.test_checkpoint_seconds)
        refresh_with_retry(update_script, scraper_dir, cache, args.timezone)
        filter_test_fixture_rows(cache, args.release_date, args.release_time)
        clear_actuals(cache)
        if not watcher_check(watcher, cache, args.release_date, input_json, pre_release=True):
            raise RuntimeError("Test calendar became incomplete before simulated release.")
        time.sleep(args.test_release_gap_seconds)
        refresh_with_retry(update_script, scraper_dir, cache, args.timezone)
        filter_test_fixture_rows(cache, args.release_date, args.release_time)
        clear_actuals(cache)
        poll_deadline = time.monotonic() + args.max_wait_minutes * 60
    else:
        # Always fetch immediately, even if the workflow starts between checkpoints.
        LOG.info("Initial calendar refresh at workflow start.")
        try:
            refresh_with_retry(update_script, scraper_dir, production_cache, args.timezone)
        except Exception:
            LOG.exception("Initial Forex Factory refresh failed; scheduled retries will continue.")
        if not watcher_check(watcher, production_cache, args.release_date,
                             input_json, pre_release=True):
            LOG.warning("Initial calendar snapshot is incomplete; will recheck at release checkpoints.")
        checkpoints = [
            ("T-15 minutes", release - timedelta(minutes=15)),
            ("T-1 minute", release - timedelta(minutes=1)),
            ("release time", release),
        ]
        now = datetime.now(tz)
        if now < release:
            for label, checkpoint in checkpoints:
                if checkpoint <= datetime.now(tz):
                    continue
                wait_until(checkpoint)
                LOG.info("Checkpoint %s: refresh calendar.", label)
                try:
                    refresh_with_retry(update_script, scraper_dir, production_cache, args.timezone)
                except Exception:
                    LOG.exception("Calendar refresh failed at %s; will retry later.", label)
                if not watcher_check(watcher, production_cache, args.release_date,
                                     input_json, pre_release=True):
                    LOG.warning("Calendar still lacks Forecast/Previous at %s; waiting for next refresh.", label)
        poll_deadline = time.monotonic() + args.max_wait_minutes * 60

    failed_checks = 0
    attempt = 0
    ready = False
    while time.monotonic() <= poll_deadline:
        attempt += 1
        LOG.info("Actual polling attempt #%d; refresh interval=%ds.", attempt, args.poll_seconds)
        if test_mode:
            # Test-only controlled release: first 7/8, then 8/8 on a later poll.
            refresh_with_retry(update_script, scraper_dir, cache, args.timezone)
            filter_test_fixture_rows(cache, args.release_date, args.release_time)
            clear_actuals(cache)
            if failed_checks == 0:
                inject_test_actuals(cache, args.release_date, list(REPORTS)[:-1])
            else:
                inject_test_actuals(cache, args.release_date, list(REPORTS))
        else:
            try:
                refresh_with_retry(update_script, scraper_dir, production_cache, args.timezone)
            except Exception:
                # A transient source/network error must not terminate the full
                # Actual wait; inspect the last valid cache and retry next cycle.
                LOG.exception("Calendar refresh failed during Actual polling; will retry.")

        if watcher_check(watcher, cache, args.release_date, input_json):
            LOG.info("All eight Actual/Forecast pairs are present. Proceeding to Stage D.")
            ready = True
            break
        failed_checks += 1
        if time.monotonic() >= poll_deadline:
            break
        time.sleep(args.poll_seconds)

    if not ready:
        raise TimeoutError(
            f"All eight Actual values did not appear within {args.max_wait_minutes} minutes. "
            "No prediction was sent."
        )

    run_prediction(predictor, model, input_json, output_dir, telegram_script,
                   release, test_mode)
    LOG.info("Pipeline completed successfully; Telegram accepted the message.")
    if test_mode:
        LOG.info("Test cache used: %s. Production cache remained untouched.", cache)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
        LOG.exception("Release orchestration failed.")
        raise SystemExit(1)
