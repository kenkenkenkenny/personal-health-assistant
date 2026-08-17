"""HTTP client and parsers for the Google Health API."""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import requests

from .google_auth import GoogleAuthError, GoogleAuthService
from .models import HeartRateStats, SleepData


LOGGER = logging.getLogger(__name__)


class GoogleHealthError(RuntimeError):
    """Raised when the Google Health API request cannot be completed."""


class GoogleHealthPermissionError(GoogleHealthError):
    """Raised for missing API scopes or permissions."""


class GoogleHealthClient:
    """Centralized, retrying Google Health REST client."""

    BASE_URL = "https://health.googleapis.com/v4"
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        auth_service: GoogleAuthService,
        *,
        session: requests.Session | None = None,
        timeout: float = 20.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.auth_service = auth_service
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        paginated_key: str | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated request, with retries, refresh and pagination."""
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        body = dict(json_body or {})
        query = dict(params or {})
        combined_items: list[Any] = []

        while True:
            refreshed_after_401 = False
            response: requests.Response | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    credentials = self.auth_service.get_credentials()
                    response = self.session.request(
                        method=method,
                        url=url,
                        headers={
                            "Authorization": f"Bearer {credentials.token}",
                            "Accept": "application/json",
                        },
                        params=query or None,
                        json=body or None,
                        timeout=self.timeout,
                    )
                except (requests.Timeout, requests.ConnectionError) as exc:
                    if attempt >= self.max_retries:
                        raise GoogleHealthError("Google Health API network request failed") from exc
                    self._sleep(2**attempt)
                    continue
                except GoogleAuthError:
                    raise

                if response.status_code == 401 and not refreshed_after_401:
                    self.auth_service.refresh_credentials()
                    refreshed_after_401 = True
                    continue
                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    if attempt >= self.max_retries:
                        raise GoogleHealthError(
                            f"Google Health API remained unavailable (HTTP {response.status_code})"
                        )
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                    self._sleep(delay)
                    continue
                break

            if response is None:
                raise GoogleHealthError("Google Health API returned no response")
            if response.status_code == 401:
                raise GoogleHealthError("Google Health authentication was rejected; run auth again")
            if response.status_code == 403:
                raise GoogleHealthPermissionError(
                    "Google Health denied access; verify the API, test user, consent, and scopes"
                )
            if not response.ok:
                raise GoogleHealthError(f"Google Health API request failed (HTTP {response.status_code})")
            try:
                payload = response.json()
            except requests.JSONDecodeError as exc:
                raise GoogleHealthError("Google Health API returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise GoogleHealthError("Google Health API returned an unexpected JSON value")

            if paginated_key is None:
                return payload
            page_items = payload.get(paginated_key, [])
            if not isinstance(page_items, list):
                raise GoogleHealthError(
                    f"Google Health API field {paginated_key!r} was not a list"
                )
            combined_items.extend(page_items)
            page_token = payload.get("nextPageToken")
            if not page_token:
                return {paginated_key: combined_items}
            if method.upper() == "GET":
                query["pageToken"] = page_token
            else:
                body["pageToken"] = page_token

    def check_steps_access(self, target_date: date) -> bool:
        """Verify GET access and report whether at least one steps point exists."""
        payload = self._request(
            "GET",
            "/users/me/dataTypes/steps/dataPoints",
            params={
                "filter": (
                    "steps.interval.civil_start_time >= "
                    f'"{target_date.isoformat()}T00:00:00"'
                ),
                "page_size": 1,
            },
        )
        data_points = payload.get("dataPoints", [])
        if not isinstance(data_points, list):
            raise GoogleHealthError("Google Health API field 'dataPoints' was not a list")
        return bool(data_points)

    def get_steps(self, target_date: date) -> int | None:
        """Return reconciled steps for one civil date, or None when unavailable."""
        next_date = target_date + timedelta(days=1)
        payload = self._request(
            "POST",
            "/users/me/dataTypes/steps/dataPoints:dailyRollUp",
            json_body={
                "range": {
                    "start": self._civil_midnight(target_date),
                    "end": self._civil_midnight(next_date),
                },
                "windowSizeDays": 1,
            },
            paginated_key="rollupDataPoints",
        )
        values: list[int] = []
        for point in payload.get("rollupDataPoints", []):
            if not isinstance(point, dict):
                continue
            steps = point.get("steps")
            if not isinstance(steps, dict) or "countSum" not in steps:
                continue
            try:
                values.append(int(steps["countSum"]))
            except (TypeError, ValueError):
                LOGGER.warning("Ignoring a malformed steps rollup value")
        return sum(values) if values else None

    def get_active_minutes(self, target_date: date) -> int | None:
        """Return total active minutes across all activity levels."""
        payload = self._daily_rollup("active-minutes", target_date)
        values: list[int] = []
        for point in payload.get("rollupDataPoints", []):
            active = point.get("activeMinutes") if isinstance(point, dict) else None
            groups = active.get("activeMinutesRollupByActivityLevel") if isinstance(active, dict) else None
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                value = self._as_non_negative_int(group.get("activeMinutesSum"))
                if value is not None:
                    values.append(value)
        return sum(values) if values else None

    def get_total_calories(self, target_date: date) -> float | None:
        """Return total daily energy expenditure in kilocalories."""
        payload = self._daily_rollup("total-calories", target_date)
        values: list[float] = []
        for point in payload.get("rollupDataPoints", []):
            calories = point.get("totalCalories") if isinstance(point, dict) else None
            value = (
                self._as_non_negative_float(calories.get("kcalSum"))
                if isinstance(calories, dict)
                else None
            )
            if value is not None:
                values.append(value)
        return sum(values) if values else None

    def get_distance(self, target_date: date) -> float | None:
        """Return daily distance in kilometers."""
        payload = self._daily_rollup("distance", target_date)
        millimeters = self._sum_rollup_values(payload, "distance", "millimetersSum")
        return millimeters / 1_000_000 if millimeters is not None else None

    def get_floors(self, target_date: date) -> int | None:
        """Return floors climbed during the civil date."""
        payload = self._daily_rollup("floors", target_date)
        value = self._sum_rollup_values(payload, "floors", "countSum")
        return round(value) if value is not None else None

    def get_active_zone_minutes(self, target_date: date) -> int | None:
        """Return Fitbit-style weighted active zone minutes."""
        payload = self._daily_rollup("active-zone-minutes", target_date)
        fields = (
            "sumInFatBurnHeartZone",
            "sumInCardioHeartZone",
            "sumInPeakHeartZone",
        )
        total = 0
        found = False
        for point in payload.get("rollupDataPoints", []):
            value = point.get("activeZoneMinutes") if isinstance(point, dict) else None
            if not isinstance(value, dict):
                continue
            for field in fields:
                parsed = self._as_non_negative_int(value.get(field))
                if parsed is not None:
                    total += parsed
                    found = True
        return total if found else None

    def get_heart_rate_stats(self, target_date: date) -> HeartRateStats | None:
        """Return all-day average, minimum and maximum heart rate."""
        payload = self._daily_rollup("heart-rate", target_date)
        averages: list[float] = []
        minimums: list[float] = []
        maximums: list[float] = []
        for point in payload.get("rollupDataPoints", []):
            value = point.get("heartRate") if isinstance(point, dict) else None
            if not isinstance(value, dict):
                continue
            average = self._as_non_negative_float(value.get("beatsPerMinuteAvg"))
            minimum = self._as_non_negative_float(value.get("beatsPerMinuteMin"))
            maximum = self._as_non_negative_float(value.get("beatsPerMinuteMax"))
            if average is not None:
                averages.append(average)
            if minimum is not None:
                minimums.append(minimum)
            if maximum is not None:
                maximums.append(maximum)
        if not averages and not minimums and not maximums:
            return None
        return HeartRateStats(
            average=statistics.fmean(averages) if averages else None,
            minimum=min(minimums) if minimums else None,
            maximum=max(maximums) if maximums else None,
        )

    def get_exercise(self, target_date: date) -> int | None:
        """Return total active workout duration in whole minutes."""
        payload = self._request(
            "GET",
            "/users/me/dataTypes/exercise/dataPoints:reconcile",
            params={
                "filter": self._range_filter(
                    "exercise.interval.civil_start_time", target_date
                ),
                "pageSize": 25,
            },
            paginated_key="dataPoints",
        )
        seconds = 0.0
        found = False
        for point in payload.get("dataPoints", []):
            exercise = point.get("exercise") if isinstance(point, dict) else None
            if not isinstance(exercise, dict):
                continue
            duration = self._parse_duration_seconds(exercise.get("activeDuration"))
            if duration is None:
                interval = exercise.get("interval")
                if isinstance(interval, dict):
                    start = self._parse_datetime(interval.get("startTime"))
                    end = self._parse_datetime(interval.get("endTime"))
                    if start is not None and end is not None and end > start:
                        duration = (end - start).total_seconds()
            if duration is not None:
                seconds += duration
                found = True
        return round(seconds / 60) if found else None

    def get_sleep(self, target_date: date) -> SleepData | None:
        """Return main sleep ending on a civil date, keeping naps separate."""
        payload = self._request(
            "GET",
            "/users/me/dataTypes/sleep/dataPoints:reconcile",
            params={
                "filter": self._range_filter(
                    "sleep.interval.civil_end_time", target_date
                ),
                "pageSize": 25,
            },
            paginated_key="dataPoints",
        )
        main_sessions: list[tuple[datetime, datetime, int | None, dict[str, int]]] = []
        nap_values: list[int] = []
        for point in payload.get("dataPoints", []):
            if not isinstance(point, dict):
                continue
            sleep = point.get("sleep")
            if not isinstance(sleep, dict):
                continue
            minutes = self._sleep_minutes(sleep)
            metadata = sleep.get("metadata")
            is_nap = isinstance(metadata, dict) and metadata.get("nap") is True
            if is_nap:
                if minutes is not None:
                    nap_values.append(minutes)
                continue
            interval = sleep.get("interval")
            if not isinstance(interval, dict):
                continue
            start = self._parse_datetime(interval.get("startTime"))
            end = self._parse_datetime(interval.get("endTime"))
            if start is None or end is None or end <= start:
                LOGGER.warning("Ignoring a malformed sleep interval")
                continue
            main_sessions.append((start, end, minutes, self._sleep_stage_minutes(sleep)))

        nap_minutes = sum(nap_values) if nap_values else None
        if not main_sessions:
            return SleepData(nap_minutes=nap_minutes) if nap_minutes is not None else None

        known_minutes = [minutes for _, _, minutes, _ in main_sessions if minutes is not None]
        stage_totals: dict[str, int] = {}
        for _, _, _, stages in main_sessions:
            for stage, value in stages.items():
                stage_totals[stage] = stage_totals.get(stage, 0) + value
        return SleepData(
            total_sleep_minutes=(
                sum(known_minutes) if len(known_minutes) == len(main_sessions) else None
            ),
            sleep_start=min(start for start, _, _, _ in main_sessions),
            sleep_end=max(end for _, end, _, _ in main_sessions),
            nap_minutes=nap_minutes,
            deep_minutes=stage_totals.get("DEEP"),
            rem_minutes=stage_totals.get("REM"),
            light_minutes=stage_totals.get("LIGHT"),
            awake_minutes=stage_totals.get("AWAKE"),
        )

    def get_resting_heart_rate(self, target_date: date) -> float | None:
        """Return the reconciled resting heart rate for a civil date."""
        payload = self._get_reconciled_daily(
            "daily-resting-heart-rate", "dailyRestingHeartRate", target_date
        )
        values = self._numeric_values(
            payload, "dailyRestingHeartRate", "beatsPerMinute", target_date
        )
        return self._mean_or_none(values, "resting heart rate")

    def get_hrv(self, target_date: date) -> float | None:
        """Return daily RMSSD HRV, falling back to same-day sample HRV."""
        payload = self._get_reconciled_daily(
            "daily-heart-rate-variability", "dailyHeartRateVariability", target_date
        )
        values = self._numeric_values(
            payload,
            "dailyHeartRateVariability",
            "averageHeartRateVariabilityMilliseconds",
            target_date,
        )
        daily_value = self._mean_or_none(values, "daily HRV")
        if daily_value is not None:
            return daily_value

        fallback = self._request(
            "GET",
            "/users/me/dataTypes/heart-rate-variability/dataPoints:reconcile",
            params={
                "filter": self._range_filter(
                    "heart_rate_variability.sample_time.civil_time", target_date
                )
            },
            paginated_key="dataPoints",
        )
        rmssd_values: list[float] = []
        sdnn_values: list[float] = []
        for point in fallback.get("dataPoints", []):
            if not isinstance(point, dict):
                continue
            hrv = point.get("heartRateVariability")
            if not isinstance(hrv, dict):
                continue
            rmssd = self._as_non_negative_float(
                hrv.get("rootMeanSquareOfSuccessiveDifferencesMilliseconds")
            )
            sdnn = self._as_non_negative_float(hrv.get("standardDeviationMilliseconds"))
            if rmssd is not None:
                rmssd_values.append(rmssd)
            elif sdnn is not None:
                sdnn_values.append(sdnn)
        if rmssd_values:
            return statistics.fmean(rmssd_values)
        if sdnn_values:
            LOGGER.warning("Daily/RMSSD HRV unavailable; using mean SDNN fallback")
            return statistics.fmean(sdnn_values)
        return None

    def get_oxygen_saturation(self, target_date: date) -> float | None:
        payload = self._get_reconciled_daily(
            "daily-oxygen-saturation", "dailyOxygenSaturation", target_date
        )
        return self._mean_or_none(
            self._numeric_values(
                payload, "dailyOxygenSaturation", "averagePercentage", target_date
            ),
            "oxygen saturation",
        )

    def get_respiratory_rate(self, target_date: date) -> float | None:
        payload = self._get_reconciled_daily(
            "daily-respiratory-rate", "dailyRespiratoryRate", target_date
        )
        return self._mean_or_none(
            self._numeric_values(
                payload, "dailyRespiratoryRate", "breathsPerMinute", target_date
            ),
            "respiratory rate",
        )

    def get_vo2_max(self, target_date: date) -> float | None:
        payload = self._get_reconciled_daily(
            "daily-vo2-max", "dailyVo2Max", target_date
        )
        return self._mean_or_none(
            self._numeric_values(payload, "dailyVo2Max", "vo2Max", target_date),
            "VO2 max",
        )

    def _get_reconciled_daily(
        self, data_type: str, field_name: str, target_date: date
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/users/me/dataTypes/{data_type}/dataPoints:reconcile",
            params={
                "filter": self._range_filter(
                    f"{data_type.replace('-', '_')}.date", target_date
                )
            },
            paginated_key="dataPoints",
        )

    def _daily_rollup(self, data_type: str, target_date: date) -> dict[str, Any]:
        next_date = target_date + timedelta(days=1)
        return self._request(
            "POST",
            f"/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp",
            json_body={
                "range": {
                    "start": self._civil_midnight(target_date),
                    "end": self._civil_midnight(next_date),
                },
                "windowSizeDays": 1,
            },
            paginated_key="rollupDataPoints",
        )

    @classmethod
    def _numeric_values(
        cls,
        payload: dict[str, Any],
        object_field: str,
        value_field: str,
        target_date: date,
    ) -> list[float]:
        values: list[float] = []
        for point in payload.get("dataPoints", []):
            if not isinstance(point, dict):
                continue
            data = point.get(object_field)
            if not isinstance(data, dict) or not cls._date_matches(data.get("date"), target_date):
                continue
            value = cls._as_non_negative_float(data.get(value_field))
            if value is not None:
                values.append(value)
        return values

    @staticmethod
    def _mean_or_none(values: list[float], label: str) -> float | None:
        if not values:
            return None
        if len(values) > 1:
            LOGGER.warning("Multiple reconciled %s values returned; using their mean", label)
        return statistics.fmean(values)

    @classmethod
    def _sleep_minutes(cls, sleep: dict[str, Any]) -> int | None:
        summary = sleep.get("summary")
        if isinstance(summary, dict):
            value = cls._as_non_negative_int(summary.get("minutesAsleep"))
            if value is not None:
                return value

        stages = sleep.get("stages")
        if not isinstance(stages, list):
            return None
        seconds = 0.0
        found = False
        for stage in stages:
            if not isinstance(stage, dict) or stage.get("type") not in {
                "LIGHT",
                "DEEP",
                "REM",
                "ASLEEP",
            }:
                continue
            start = cls._parse_datetime(stage.get("startTime"))
            end = cls._parse_datetime(stage.get("endTime"))
            if start is not None and end is not None and end > start:
                seconds += (end - start).total_seconds()
                found = True
        return round(seconds / 60) if found else None

    @classmethod
    def _sleep_stage_minutes(cls, sleep: dict[str, Any]) -> dict[str, int]:
        summary = sleep.get("summary")
        stage_values: dict[str, int] = {}
        if isinstance(summary, dict) and isinstance(summary.get("stagesSummary"), list):
            for stage in summary["stagesSummary"]:
                if not isinstance(stage, dict) or not isinstance(stage.get("type"), str):
                    continue
                minutes = cls._as_non_negative_int(stage.get("minutes"))
                if minutes is not None:
                    # Reconciled responses can contain duplicate summaries for a stage.
                    # The schema defines one summary per type, so retain the largest value.
                    stage_values[stage["type"]] = max(
                        stage_values.get(stage["type"], 0), minutes
                    )
        return stage_values

    @classmethod
    def _sum_rollup_values(
        cls, payload: dict[str, Any], object_field: str, value_field: str
    ) -> float | None:
        values: list[float] = []
        for point in payload.get("rollupDataPoints", []):
            value = point.get(object_field) if isinstance(point, dict) else None
            parsed = (
                cls._as_non_negative_float(value.get(value_field))
                if isinstance(value, dict)
                else None
            )
            if parsed is not None:
                values.append(parsed)
        return sum(values) if values else None

    @staticmethod
    def _range_filter(field: str, target_date: date) -> str:
        next_date = target_date + timedelta(days=1)
        return (
            f'{field} >= "{target_date.isoformat()}" AND '
            f'{field} < "{next_date.isoformat()}"'
        )

    @staticmethod
    def _date_matches(value: Any, target_date: date) -> bool:
        if not isinstance(value, dict):
            return False
        return (
            value.get("year") == target_date.year
            and value.get("month") == target_date.month
            and value.get("day") == target_date.day
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _as_non_negative_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed >= 0 else None

    @staticmethod
    def _as_non_negative_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _parse_duration_seconds(value: Any) -> float | None:
        if not isinstance(value, str) or not value.endswith("s"):
            return None
        try:
            seconds = float(value[:-1])
        except ValueError:
            return None
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    @staticmethod
    def _civil_midnight(value: date) -> dict[str, dict[str, int]]:
        return {
            "date": {"year": value.year, "month": value.month, "day": value.day},
            "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
        }
