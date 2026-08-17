"""APScheduler adapter for the directly testable daily workflow."""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .report_service import ReportService


LOGGER = logging.getLogger(__name__)


def run_scheduler(config: Config, report_service: ReportService) -> None:
    hour, minute = (int(part) for part in config.report_time.split(":"))
    scheduler = BlockingScheduler(timezone=config.timezone)
    scheduler.add_job(
        report_service.run_daily_health_report,
        CronTrigger(hour=hour, minute=minute, timezone=config.timezone),
        id="daily-health-report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    LOGGER.info(
        "Scheduler started: daily at %s (%s)", config.report_time, config.timezone
    )
    scheduler.start()
