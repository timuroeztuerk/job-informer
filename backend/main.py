#!/usr/bin/env python3
"""Run one manual LinkedIn collection."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from loguru import logger


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

from src.agents.job_scraper import JobScraper  # noqa: E402
from src.config.settings import Config  # noqa: E402
from src.utils.logging_utils import setup_logging  # noqa: E402
from src.utils.run_progress import update_api_run_progress  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect LinkedIn jobs once")
    parser.add_argument("--run-once", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", help="Path to an optional .env file")
    parser.add_argument("--keywords", help="Comma-separated LinkedIn search titles")
    parser.add_argument("--locations", help="Comma-separated search locations")
    parser.add_argument("--time", dest="time_range", choices=["day", "week", "month"])
    return parser.parse_args()


def run_job_search_once(config: Config) -> None:
    update_api_run_progress(
        stage="preparing",
        label="Preparing LinkedIn collection",
        event="Collection settings loaded",
    )
    scraper = JobScraper(config)
    summary = scraper.get_search_summary()
    logger.info(
        "Starting LinkedIn collection: keywords={}, locations={}, time_range={}",
        ", ".join(summary["keywords"]),
        ", ".join(summary["locations"]),
        summary["time_range"],
    )

    success = scraper.execute_job_search()
    if success:
        update_api_run_progress(
            stage="completed",
            label="Collection completed",
            event="Collection completed",
        )
        logger.success("LinkedIn collection completed")
        return
    if scraper.last_run_failed:
        raise RuntimeError("LinkedIn collection failed")

    update_api_run_progress(
        stage="completed",
        label="Collection completed without eligible new jobs",
        event="Collection completed without eligible new jobs",
        event_level="warning",
    )
    logger.warning("LinkedIn collection completed without eligible new jobs")


def main() -> None:
    args = parse_args()
    setup_logging()
    try:
        config = Config.from_env(args.config)
        setup_logging(config.log_level, config.log_file)
        if args.keywords:
            config.search_keywords = args.keywords
        if args.locations:
            config.search_locations = args.locations
        if args.time_range:
            config.search_time_range = args.time_range
        config.validate_for_mode("run-once")
        (ROOT / "data").mkdir(exist_ok=True)
        run_job_search_once(config)
    except Exception as exc:
        update_api_run_progress(
            stage="failed",
            label="Collection stopped with an error",
            event=f"Collection failed: {exc}",
            event_level="error",
        )
        logger.error("Application error: {}", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
