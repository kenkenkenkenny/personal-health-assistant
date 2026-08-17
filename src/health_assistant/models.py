"""Normalized health domain models."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class SleepData(BaseModel):
    """Normalized main-sleep result with naps kept separate."""

    model_config = ConfigDict(extra="forbid")

    total_sleep_minutes: int | None = Field(default=None, ge=0)
    sleep_start: datetime | None = None
    sleep_end: datetime | None = None
    nap_minutes: int | None = Field(default=None, ge=0)
    deep_minutes: int | None = Field(default=None, ge=0)
    rem_minutes: int | None = Field(default=None, ge=0)
    light_minutes: int | None = Field(default=None, ge=0)
    awake_minutes: int | None = Field(default=None, ge=0)


class HeartRateStats(BaseModel):
    """Daily heart-rate rollup."""

    model_config = ConfigDict(extra="forbid")

    average: float | None = Field(default=None, ge=0)
    minimum: float | None = Field(default=None, ge=0)
    maximum: float | None = Field(default=None, ge=0)


class DailyHealthSummary(BaseModel):
    """Normalized health metrics for one civil date."""

    model_config = ConfigDict(extra="forbid")

    date: date
    steps: int | None = Field(default=None, ge=0)
    sleep_minutes: int | None = Field(default=None, ge=0)
    sleep_start: datetime | None = None
    sleep_end: datetime | None = None
    sleep_deep_minutes: int | None = Field(default=None, ge=0)
    sleep_rem_minutes: int | None = Field(default=None, ge=0)
    sleep_light_minutes: int | None = Field(default=None, ge=0)
    sleep_awake_minutes: int | None = Field(default=None, ge=0)
    resting_heart_rate: float | None = Field(default=None, ge=0)
    heart_rate_average: float | None = Field(default=None, ge=0)
    heart_rate_minimum: float | None = Field(default=None, ge=0)
    heart_rate_maximum: float | None = Field(default=None, ge=0)
    hrv_ms: float | None = Field(default=None, ge=0)
    calories: float | None = Field(default=None, ge=0)
    active_minutes: int | None = Field(default=None, ge=0)
    exercise_minutes: int | None = Field(default=None, ge=0)
    distance_km: float | None = Field(default=None, ge=0)
    floors: int | None = Field(default=None, ge=0)
    active_zone_minutes: int | None = Field(default=None, ge=0)
    oxygen_saturation_percent: float | None = Field(default=None, ge=0, le=100)
    respiratory_rate: float | None = Field(default=None, ge=0)
    vo2_max: float | None = Field(default=None, ge=0)
    data_quality: dict[str, str] = Field(default_factory=dict)
