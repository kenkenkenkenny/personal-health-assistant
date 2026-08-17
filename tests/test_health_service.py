from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import Mock

from health_assistant.health_service import HealthService
from health_assistant.models import HeartRateStats, SleepData


def test_health_service_normalizes_all_metrics() -> None:
    client = Mock()
    client.get_steps.return_value = 8234
    client.get_sleep.return_value = SleepData(
        total_sleep_minutes=434,
        sleep_start=datetime(2026, 8, 15, 22, tzinfo=timezone.utc),
        sleep_end=datetime(2026, 8, 16, 6, tzinfo=timezone.utc),
        deep_minutes=80,
        rem_minutes=90,
        light_minutes=264,
        awake_minutes=46,
    )
    client.get_resting_heart_rate.return_value = 61
    client.get_hrv.return_value = 46.2
    client.get_total_calories.return_value = 2100.5
    client.get_active_minutes.return_value = 38
    client.get_exercise.return_value = 25
    client.get_distance.return_value = 6.2
    client.get_floors.return_value = 10
    client.get_active_zone_minutes.return_value = 22
    client.get_heart_rate_stats.return_value = HeartRateStats(
        average=75, minimum=48, maximum=145
    )
    client.get_oxygen_saturation.return_value = 96.5
    client.get_respiratory_rate.return_value = 15.4
    client.get_vo2_max.return_value = 43.2

    summary = HealthService(client).get_daily_health(date(2026, 8, 16))

    assert summary.steps == 8234
    assert summary.sleep_minutes == 434
    assert summary.resting_heart_rate == 61.0
    assert summary.hrv_ms == 46.2
    assert summary.calories == 2100.5
    assert summary.active_minutes == 38
    assert summary.exercise_minutes == 25
    assert summary.sleep_deep_minutes == 80
    assert summary.distance_km == 6.2
    assert summary.heart_rate_maximum == 145
    assert summary.oxygen_saturation_percent == 96.5
    assert summary.respiratory_rate == 15.4
    assert summary.vo2_max == 43.2
    assert set(summary.data_quality.values()) == {"available"}


def test_health_service_isolates_partial_failures_and_missing_data() -> None:
    client = Mock()
    client.get_steps.return_value = 100
    client.get_sleep.return_value = None
    client.get_resting_heart_rate.side_effect = RuntimeError("upstream failure")
    client.get_hrv.return_value = 42.0
    client.get_total_calories.return_value = None
    client.get_active_minutes.return_value = None
    client.get_exercise.return_value = None
    client.get_distance.return_value = None
    client.get_floors.return_value = None
    client.get_active_zone_minutes.return_value = None
    client.get_heart_rate_stats.return_value = None
    client.get_oxygen_saturation.return_value = None
    client.get_respiratory_rate.return_value = None
    client.get_vo2_max.return_value = None

    summary = HealthService(client).get_daily_health(date(2026, 8, 16))

    assert summary.steps == 100
    assert summary.sleep_minutes is None
    assert summary.resting_heart_rate is None
    assert summary.hrv_ms == 42.0
    assert summary.data_quality == {
        "steps": "available",
        "sleep": "missing",
        "resting_heart_rate": "error",
        "hrv": "available",
        "calories": "missing",
        "active_minutes": "missing",
        "exercise": "missing",
        "distance": "missing",
        "floors": "missing",
        "active_zone_minutes": "missing",
        "heart_rate": "missing",
        "oxygen_saturation": "missing",
        "respiratory_rate": "missing",
        "vo2_max": "missing",
    }
