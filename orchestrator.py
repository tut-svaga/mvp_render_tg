#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrate the eight-report XAUUSD release and isolated end-to-end test."""
from __future__ import annotations
import argparse, csv, logging, shutil, subprocess, sys, time
from datetime import datetime, timedelta
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

def run_update(script: Path, scraper_dir: Path, cache: Path, timezone: str) -> None:
    if not script.is_file():
        raise FileNotFoundError(f"Forex Factory updater not found: {script}")
    command = [sys.executable, str(script), "--cache", str(cache), "--timezone", timezone]
    LOG.info("Running real Forex Factory scraper: %s", " ".join(command))
    result = subprocess.run(command, cwd=scraper_dir)
    if result.returncode:
        raise RuntimeError(f"Forex Factory update.py exited with code {result.returncode}")

def check_actuals(watcher: Path, cache: Path, release_date: str, output_json: Path) -> bool:
    result = subprocess.run([sys.executable, str(watcher), "--cache", str(cache),
        "--date", release_date, "--output-json", str(output_json), "--once"], cwd=ROOT)
    if result.returncode == 0: return True
    if result.returncode == 2: return False
    raise RuntimeError(f"stage_d_cache_watcher.py failed with code {result.returncode}")

def clear_test_actuals(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    with destination.open("r", encoding="utf-8-sig", newline="") as f: rows = list(csv.reader(f))
    if not rows: raise ValueError(f"Test cache template is empty: {source}")
    indices = [i for i, name in enumerate(rows[0]) if name.strip().lower() == "actual"]
    if not indices: raise ValueError("Test cache template has no Actual column.")
    for row in rows[1:]:
        for i in indices:
            if i < len(row): row[i] = ""
    with destination.open("w", encoding="utf-8", newline="") as f: csv.writer(f).writerows(rows)
    LOG.info("Prepared isolated test cache; cleared Actual values in %d column(s).", len(indices))

def inject_synthetic_actuals(cache: Path, release_date: str, report_names=None) -> None:
    with cache.open("r", encoding="utf-8-sig", newline="") as f: rows = list(csv.reader(f))
    header = [v.strip().lower() for v in rows[0]]
    event_i = [i for i, v in enumerate(header) if v == "event"]
    date_i = [i for i, v in enumerate(header) if v in {"datetime", "date", "datetime_local", "datetime_utc"}]
    actual_i = [i for i, v in enumerate(header) if v == "actual"]
    if not event_i or not actual_i: raise ValueError("Test cache must contain Event and Actual columns.")
    targets = set(REPORTS if report_names is None else report_names)
    injected = set()
    for row in rows[1:]:
        event = next((row[i].strip() for i in event_i if i < len(row) and row[i].strip()), "")
        if event not in targets: continue
        row_date = next((row[i].strip()[:10] for i in date_i if i < len(row) and row[i].strip()), "")
        if row_date != release_date: continue
        for i in actual_i:
            if i < len(row): row[i] = REPORTS[event]
        injected.add(event)
    missing = sorted(targets - injected)
    if missing: raise RuntimeError("Missing reports in test cache: " + ", ".join(missing))
    with cache.open("w", encoding="utf-8", newline="") as f: csv.writer(f).writerows(rows)
    LOG.warning("TEST MODE: synthetic Actual values injected for %d/%d reports: %s",
                len(injected), len(REPORTS), ", ".join(sorted(injected)))

def main() -> int:
    p = argparse.ArgumentParser(description="Run the eight-report XAUUSD release pipeline.")
    p.add_argument("--mode", choices=["release", "test"], default="release")
    p.add_argument("--release-date", required=True)
    p.add_argument("--release-time", default="15:30")
    p.add_argument("--timezone", default="Europe/Chisinau")
    p.add_argument("--scraper-dir", type=Path, default=ROOT / "forexfactory-scraper")
    p.add_argument("--update-script", type=Path, default=ROOT / "forexfactory-scraper" / "update.py")
    p.add_argument("--cache", type=Path, default=ROOT / "data" / "forex_factory_cache.csv")
    p.add_argument("--test-cache-template", type=Path, default=ROOT / "data" / "forex_factory_cache_test.csv")
    p.add_argument("--test-cache", type=Path, default=ROOT / "data" / "forex_factory_cache_test_run.csv")
    p.add_argument("--model", type=Path, default=ROOT / "stage_d_model.json")
    p.add_argument("--watcher", type=Path, default=ROOT / "stage_d_cache_watcher.py")
    p.add_argument("--predictor", type=Path, default=ROOT / "stage_d_predict_existing.py")
    p.add_argument("--telegram-script", type=Path, default=ROOT / "telegram_mvp.py")
    p.add_argument("--output-dir", type=Path, default=ROOT / "output_stage_d")
    p.add_argument("--poll-seconds", type=float, default=60)
    p.add_argument("--max-wait-minutes", type=float, default=75)
    p.add_argument("--test-pre-release-seconds", type=float, default=10)
    p.add_argument("--test-to-release-seconds", type=float, default=5)
    p.add_argument("--test-poll-seconds", type=float, default=5)
    p.add_argument("--test-inject-after-polls", type=int, default=2)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    try:
        release = datetime.strptime(f"{a.release_date} {a.release_time}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(a.timezone))
    except Exception as exc: p.error(f"Invalid release date/time/timezone: {exc}")
    def absolute(path): return path if path.is_absolute() else (ROOT / path).resolve()
    scraper_dir, update_script = absolute(a.scraper_dir), absolute(a.update_script)
    cache, model = absolute(a.cache), absolute(a.model)
    watcher, predictor, telegram = absolute(a.watcher), absolute(a.predictor), absolute(a.telegram_script)
    output = absolute(a.output_dir)
    actuals_json = output / ("stage_d_actuals_test.json" if a.mode == "test" else "stage_d_actuals.json")
    if not scraper_dir.is_dir(): raise FileNotFoundError(f"Scraper directory not found: {scraper_dir}")
    if not model.is_file(): raise FileNotFoundError(f"Stage D model not found: {model}")
    if a.mode == "test":
        if a.test_inject_after_polls < 1 or a.test_poll_seconds <= 0: p.error("Test polling values must be positive.")
        cache = absolute(a.test_cache)
        clear_test_actuals(absolute(a.test_cache_template), cache)
        LOG.warning("=== TEST MODE: production cache is isolated and will not be modified ===")
        LOG.info("SIMULATED T-15 refresh")
        run_update(update_script, scraper_dir, cache, a.timezone)
        time.sleep(max(0, a.test_pre_release_seconds))
        LOG.info("SIMULATED T-1 refresh")
        run_update(update_script, scraper_dir, cache, a.timezone)
        time.sleep(max(0, a.test_to_release_seconds))
        LOG.info("SIMULATED RELEASE %s %s refresh", a.release_date, a.release_time)
        run_update(update_script, scraper_dir, cache, a.timezone)
        poll_seconds, deadline = a.test_poll_seconds, time.monotonic() + 180
    else:
        if a.poll_seconds <= 0 or a.max_wait_minutes <= 0: p.error("Polling values must be positive.")
        now = datetime.now(release.tzinfo)
        checkpoints = [("T-15 minutes", release-timedelta(minutes=15)),
                       ("T-1 minute", release-timedelta(minutes=1)), ("release time", release)]
        if now < release:
            for label, point in checkpoints:
                if point < datetime.now(release.tzinfo): continue
                time.sleep(max(0, (point-datetime.now(release.tzinfo)).total_seconds()))
                LOG.info("Checkpoint %s", label)
                run_update(update_script, scraper_dir, cache, a.timezone)
        else:
            LOG.info("Started at/after release; immediate refresh")
            run_update(update_script, scraper_dir, cache, a.timezone)
        poll_seconds, deadline = a.poll_seconds, time.monotonic() + a.max_wait_minutes*60
    output.mkdir(parents=True, exist_ok=True)
    failed_checks, attempt, ready = 0, 0, False
    while time.monotonic() <= deadline:
        attempt += 1
        if a.mode == "test":
            LOG.info("TEST post-release poll #%d: refreshing scraper", attempt)
            run_update(update_script, scraper_dir, cache, a.timezone)
            if failed_checks == a.test_inject_after_polls:
                # Deliberately publish only seven values first. The watcher must
                # report the eighth as missing and keep waiting for the next poll.
                inject_synthetic_actuals(cache, a.release_date, list(REPORTS)[:-1])
            elif failed_checks > a.test_inject_after_polls:
                inject_synthetic_actuals(cache, a.release_date)
        elif attempt > 1:
            LOG.info("Post-release poll #%d: refreshing scraper", attempt)
            run_update(update_script, scraper_dir, cache, a.timezone)
        if check_actuals(watcher, cache, a.release_date, actuals_json):
            LOG.info("All eight Actual values are present; running existing model.")
            ready = True
            break
        failed_checks += 1
        time.sleep(poll_seconds)
    if not ready: raise TimeoutError("All eight Actual/Forecast pairs did not become available before timeout.")
    subprocess.run([sys.executable, str(predictor), "--model", str(model),
        "--input-json", str(actuals_json), "--output-dir", str(output)], cwd=ROOT, check=True)
    message_path = output / "stage_d_telegram_message.html"
    message = message_path.read_text(encoding="utf-8")
    prefix = "<b>TEST — синтетические Actual, не реальные данные</b>\n" if a.mode == "test" else ""
    message_path.write_text(prefix + f"<b>Публикация: {release.strftime('%d.%m.%Y %H:%M %Z')}</b>\n\n" + message, encoding="utf-8")
    subprocess.run([sys.executable, str(telegram)], cwd=ROOT, check=True)
    LOG.info("Pipeline completed; Telegram send request succeeded.")
    if a.mode == "test": LOG.info("Production cache untouched. Test cache: %s", cache)
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
        LOG.exception("Release orchestration failed.")
        raise SystemExit(1)
