"""Fault-tolerant health-data orchestration and normalization."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import TypeVar

from .google_health import GoogleHealthClient
from .models import DailyHealthSummary, HeartRateStats, SleepData


LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class HealthService:
    """Collect independent Google metrics into one normalized daily summary."""

    def __init__(self, client: GoogleHealthClient) -> None:
        self.client = client

    def get_daily_health(self, target_date: date) -> DailyHealthSummary:
        """Fetch all Phase 2 metrics without allowing one failure to abort the rest."""
        LOGGER.info("Fetching health data for %s", target_date.isoformat())
        quality: dict[str, str] = {}

        steps = self._fetch("steps", lambda: self.client.get_steps(target_date), quality)
        sleep = self._fetch("sleep", lambda: self.client.get_sleep(target_date), quality)
        resting_hr = self._fetch(
            "resting_heart_rate",
            lambda: self.client.get_resting_heart_rate(target_date),
            quality,
        )
        hrv = self._fetch("hrv", lambda: self.client.get_hrv(target_date), quality)
        calories = self._fetch(
            "calories", lambda: self.client.get_total_calories(target_date), quality
        )
        active_minutes = self._fetch(
            "active_minutes", lambda: self.client.get_active_minutes(target_date), quality
        )
        exercise_minutes = self._fetch(
            "exercise", lambda: self.client.get_exercise(target_date), quality
        )
        distance = self._fetch(
            "distance", lambda: self.client.get_distance(target_date), quality
        )
        floors = self._fetch("floors", lambda: self.client.get_floors(target_date), quality)
        active_zone_minutes = self._fetch(
            "active_zone_minutes",
            lambda: self.client.get_active_zone_minutes(target_date),
            quality,
        )
        heart_rate_stats = self._fetch(
            "heart_rate", lambda: self.client.get_heart_rate_stats(target_date), quality
        )
        oxygen_saturation = self._fetch(
            "oxygen_saturation",
            lambda: self.client.get_oxygen_saturation(target_date),
            quality,
        )
        respiratory_rate = self._fetch(
            "respiratory_rate",
            lambda: self.client.get_respiratory_rate(target_date),
            quality,
        )
        vo2_max = self._fetch(
            "vo2_max", lambda: self.client.get_vo2_max(target_date), quality
        )

        sleep_data = sleep if isinstance(sleep, SleepData) else None
        if sleep_data is not None and sleep_data.total_sleep_minutes is None:
            quality["sleep"] = "missing"
        heart_data = heart_rate_stats if isinstance(heart_rate_stats, HeartRateStats) else None
        return DailyHealthSummary(
            date=target_date,
            steps=steps if isinstance(steps, int) else None,
            sleep_minutes=sleep_data.total_sleep_minutes if sleep_data else None,
            sleep_start=sleep_data.sleep_start if sleep_data else None,
            sleep_end=sleep_data.sleep_end if sleep_data else None,
            sleep_deep_minutes=sleep_data.deep_minutes if sleep_data else None,
            sleep_rem_minutes=sleep_data.rem_minutes if sleep_data else None,
            sleep_light_minutes=sleep_data.light_minutes if sleep_data else None,
            sleep_awake_minutes=sleep_data.awake_minutes if sleep_data else None,
            resting_heart_rate=(
                float(resting_hr) if isinstance(resting_hr, (int, float)) else None
            ),
            heart_rate_average=heart_data.average if heart_data else None,
            heart_rate_minimum=heart_data.minimum if heart_data else None,
            heart_rate_maximum=heart_data.maximum if heart_data else None,
            hrv_ms=float(hrv) if isinstance(hrv, (int, float)) else None,
            calories=float(calories) if isinstance(calories, (int, float)) else None,
            active_minutes=active_minutes if isinstance(active_minutes, int) else None,
            exercise_minutes=(exercise_minutes if isinstance(exercise_minutes, int) else None),
            distance_km=float(distance) if isinstance(distance, (int, float)) else None,
            floors=floors if isinstance(floors, int) else None,
            active_zone_minutes=(
                active_zone_minutes if isinstance(active_zone_minutes, int) else None
            ),
            oxygen_saturation_percent=(
                float(oxygen_saturation)
                if isinstance(oxygen_saturation, (int, float))
                else None
            ),
            respiratory_rate=(
                float(respiratory_rate)
                if isinstance(respiratory_rate, (int, float))
                else None
            ),
            vo2_max=float(vo2_max) if isinstance(vo2_max, (int, float)) else None,
            data_quality=quality,
        )

    @staticmethod
    def _fetch(
        name: str, operation: Callable[[], T | None], quality: dict[str, str]
    ) -> T | None:
        try:
            value = operation()
        except Exception as exc:
            quality[name] = "error"
            LOGGER.warning("%s unavailable: %s", name, type(exc).__name__)
            return None
        quality[name] = "available" if value is not None else "missing"
        if value is None:
            LOGGER.warning("%s unavailable", name)
        else:
            LOGGER.info("%s fetched", name)
        return value
