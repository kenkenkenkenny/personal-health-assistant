from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from health_assistant.database import DailyHealthRecord, HealthDatabase
from health_assistant.models import DailyHealthSummary


def test_database_upserts_without_duplicates() -> None:
    database = HealthDatabase("sqlite:///:memory:")
    database.initialize()
    target = date(2026, 8, 16)
    database.save_daily_health(DailyHealthSummary(date=target, steps=100))
    database.save_daily_health(DailyHealthSummary(date=target, steps=250))

    saved = database.get_daily_health(target)
    assert saved is not None
    assert saved.steps == 250
    with database._sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyHealthRecord)) == 1


def test_database_returns_ordered_range_and_last_n_days() -> None:
    database = HealthDatabase("sqlite:///:memory:")
    database.initialize()
    end = date(2026, 8, 16)
    for offset in range(5):
        current = end - timedelta(days=offset)
        database.save_daily_health(DailyHealthSummary(date=current, steps=offset))

    result = database.get_last_n_days(3, end_date=end)
    assert [item.date for item in result] == [
        date(2026, 8, 14),
        date(2026, 8, 15),
        date(2026, 8, 16),
    ]


def test_database_persists_extended_metrics() -> None:
    database = HealthDatabase("sqlite:///:memory:")
    database.initialize()
    target = date(2026, 8, 17)
    database.save_daily_health(
        DailyHealthSummary(
            date=target,
            sleep_deep_minutes=82,
            distance_km=5.4,
            heart_rate_average=74.2,
            oxygen_saturation_percent=96.8,
            respiratory_rate=15.1,
            vo2_max=44.0,
        )
    )

    saved = database.get_daily_health(target)
    assert saved is not None
    assert saved.sleep_deep_minutes == 82
    assert saved.distance_km == 5.4
    assert saved.oxygen_saturation_percent == 96.8
