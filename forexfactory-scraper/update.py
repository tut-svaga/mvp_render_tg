#!/usr/bin/env python3
"""Refresh a rolling Forex Factory calendar window into the parent project's cache."""
from __future__ import annotations
import argparse
from datetime import date, timedelta
from pathlib import Path
from src.forexfactory.main import main as scraper_main

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
DEFAULT_CACHE = PROJECT_ROOT / "data" / "forex_factory_cache.csv"

def main() -> int:
    parser = argparse.ArgumentParser(description="Update the shared Forex Factory calendar cache.")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--past-days", type=int, default=7)
    parser.add_argument("--future-days", type=int, default=14)
    parser.add_argument("--timezone", default="Europe/Chisinau")
    args = parser.parse_args()
    if args.past_days < 0 or args.future_days < 0:
        parser.error("--past-days and --future-days must be zero or greater")
    today = date.today()
    start, end = today - timedelta(days=args.past_days), today + timedelta(days=args.future_days)
    return scraper_main(["--start", start.isoformat(), "--end", end.isoformat(),
        "--csv", str(args.cache.resolve()), "--tz", args.timezone,
        "--keep-currencies", "USD"]) or 0

if __name__ == "__main__":
    raise SystemExit(main())
