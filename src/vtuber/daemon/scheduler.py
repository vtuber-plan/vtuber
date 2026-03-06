"""APScheduler integration for scheduled tasks."""
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore


class TaskScheduler:
    """Manages scheduled tasks using APScheduler."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        jobstores = {"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")}
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)

    def start(self):
        """Start the scheduler."""
        self.scheduler.start()

    def shutdown(self):
        """Shutdown the scheduler."""
        self.scheduler.shutdown(wait=False)
