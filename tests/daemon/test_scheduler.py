import pytest
from pathlib import Path
from vtuber.daemon.scheduler import TaskScheduler


def test_scheduler_init(tmp_path):
    db_path = tmp_path / "test.db"
    scheduler = TaskScheduler(db_path)
    assert scheduler.db_path == db_path
