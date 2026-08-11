"""
APScheduler-based pipeline scheduler.
Triggers the full pipeline daily or weekly depending on SCHEDULER_DAY_OF_WEEK.
"""
import logging
from typing import Callable

logger = logging.getLogger("PipelineScheduler")


def start_scheduler(pipeline_fn: Callable) -> None:
    """
    Start the background APScheduler and add the pipeline job.
    Schedule is daily if SCHEDULER_DAY_OF_WEEK='*', otherwise weekly on the specified day.

    Args:
        pipeline_fn: The callable to execute (e.g., workflow.run_daily_workflow)
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron        import CronTrigger
        from src.config import (
            SCHEDULER_TIMEZONE,
            SCHEDULER_DAY_OF_WEEK,
            SCHEDULER_HOUR,
            SCHEDULER_MINUTE,
        )

        scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)

        is_daily = SCHEDULER_DAY_OF_WEEK.strip() == "*"
        schedule_label = "daily" if is_daily else f"weekly on {SCHEDULER_DAY_OF_WEEK.upper()}"

        scheduler.add_job(
            _run_with_logging(pipeline_fn, schedule_label),
            trigger=CronTrigger(
                day_of_week=SCHEDULER_DAY_OF_WEEK,
                hour=SCHEDULER_HOUR,
                minute=SCHEDULER_MINUTE,
                timezone=SCHEDULER_TIMEZONE,
            ),
            id="startup_pipeline",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        scheduler.start()

        next_run = scheduler.get_job("startup_pipeline").next_run_time
        logger.info(
            f"[OK] Scheduler started - pipeline runs {schedule_label} "
            f"at {SCHEDULER_HOUR:02d}:{SCHEDULER_MINUTE:02d} {SCHEDULER_TIMEZONE}. "
            f"Next run: {next_run}"
        )
        return scheduler

    except ImportError:
        logger.error("APScheduler not installed. Run: pip install APScheduler")
        return None
    except Exception as e:
        logger.error(f"Scheduler failed to start: {e}")
        return None


def _run_with_logging(fn: Callable, schedule_label: str) -> Callable:
    """Wrap the pipeline function with structured logging."""
    def wrapper():
        logger.info("=" * 60)
        logger.info(f"SCHEDULED PIPELINE TRIGGERED ({schedule_label} at {SCHEDULER_HOUR:02d}:{SCHEDULER_MINUTE:02d} {SCHEDULER_TIMEZONE})")
        logger.info("=" * 60)
        try:
            result = fn()
            logger.info(f"[OK] Scheduled pipeline completed: {result}")
        except Exception as e:
            logger.error(f"[FAIL] Scheduled pipeline FAILED: {e}", exc_info=True)
    return wrapper
