"""Python trend calculation and AIHubMix Responses API explanation."""

from __future__ import annotations

import json
import logging
import statistics
from datetime import timedelta
from typing import Any

from openai import OpenAI

from .config import Config
from .models import DailyHealthSummary


LOGGER = logging.getLogger(__name__)
METRICS = (
    "steps",
    "sleep_minutes",
    "resting_heart_rate",
    "hrv_ms",
    "active_minutes",
    "distance_km",
    "active_zone_minutes",
    "oxygen_saturation_percent",
    "respiratory_rate",
    "vo2_max",
)

SYSTEM_PROMPT = """You are a cautious personal health data assistant.
Summarize fitness and wellness trends from wearable data in Chinese.
Distinguish observations from interpretations and compare values with the user's own baseline.
Mention missing data explicitly. Never fabricate values. Do not diagnose disease or claim a single
metric proves a condition. Avoid unnecessary alarm. Focus on trends, give low-risk practical
suggestions, and recommend professional advice only when appropriate.
Sleep total excludes awake minutes. Do not add awake minutes to sleeping stages and then describe
the difference as a data inconsistency; small stage-vs-total differences can be rounding.
Return 400-800 Chinese characters using these sections: date title, activity, sleep stages,
heart rate and HRV, recovery metrics, overall trend, and 1-3 suggestions. Do not use Markdown tables."""


class HealthAnalysisError(RuntimeError):
    """Raised when an AI health report cannot be generated."""


def calculate_analysis_payload(
    today: DailyHealthSummary, history: list[DailyHealthSummary]
) -> dict[str, Any]:
    """Calculate all statistics locally; the LLM only explains them."""
    by_date = {item.date: item for item in history}
    by_date[today.date] = today
    current_dates = [today.date - timedelta(days=offset) for offset in range(7)]
    previous_dates = [today.date - timedelta(days=offset) for offset in range(7, 14)]
    current = [by_date[value] for value in current_dates if value in by_date]
    previous = [by_date[value] for value in previous_dates if value in by_date]
    current_stats = _window_stats(current)
    previous_stats = _window_stats(previous)
    trends = {
        metric: _trend(current_stats[metric]["average"], previous_stats[metric]["average"])
        for metric in METRICS
    }
    return {
        "today": {
            "date": today.date.isoformat(),
            "steps": today.steps,
            "sleep_hours": (
                round(today.sleep_minutes / 60, 2) if today.sleep_minutes is not None else None
            ),
            "resting_hr": today.resting_heart_rate,
            "hrv_ms": today.hrv_ms,
            "calories": today.calories,
            "active_minutes": today.active_minutes,
            "exercise_minutes": today.exercise_minutes,
            "distance_km": today.distance_km,
            "floors": today.floors,
            "active_zone_minutes": today.active_zone_minutes,
            "sleep_stages_minutes": {
                "deep": today.sleep_deep_minutes,
                "rem": today.sleep_rem_minutes,
                "light": today.sleep_light_minutes,
                "awake": today.sleep_awake_minutes,
            },
            "heart_rate": {
                "average": today.heart_rate_average,
                "minimum": today.heart_rate_minimum,
                "maximum": today.heart_rate_maximum,
            },
            "oxygen_saturation_percent": today.oxygen_saturation_percent,
            "respiratory_rate": today.respiratory_rate,
            "vo2_max": today.vo2_max,
            "data_quality": today.data_quality,
        },
        "7_day_stats": current_stats,
        "previous_7_day_stats": previous_stats,
        "trends": trends,
    }


def _window_stats(items: list[DailyHealthSummary]) -> dict[str, dict[str, float | int | None]]:
    result: dict[str, dict[str, float | int | None]] = {}
    for metric in METRICS:
        values = [float(value) for item in items if (value := getattr(item, metric)) is not None]
        result[metric] = {
            "average": round(statistics.fmean(values), 2) if values else None,
            "min": round(min(values), 2) if values else None,
            "max": round(max(values), 2) if values else None,
            "count": len(values),
        }
    return result


def _trend(current: float | int | None, previous: float | int | None) -> str:
    if current is None or previous is None:
        return "unknown"
    if current == previous == 0:
        return "stable"
    denominator = abs(float(previous)) or 1.0
    relative_change = (float(current) - float(previous)) / denominator
    if abs(relative_change) <= 0.03:
        return "stable"
    return "up" if relative_change > 0 else "down"


class HealthAnalyzer:
    """Send only precomputed summaries to AIHubMix via the OpenAI SDK."""

    def __init__(self, config: Config, *, client: OpenAI | None = None) -> None:
        config.validate_aihubmix()
        self.model = config.aihubmix_model
        self.client = client or OpenAI(
            api_key=config.aihubmix_api_key,
            base_url=config.aihubmix_base_url,
            timeout=45.0,
            max_retries=2,
        )

    def analyze(self, payload: dict[str, Any]) -> str:
        LOGGER.info("Generating report with AIHubMix Responses API")
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=SYSTEM_PROMPT,
                input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                max_output_tokens=1200,
            )
            report = response.output_text.strip()
        except Exception as exc:
            raise HealthAnalysisError("AIHubMix report generation failed") from exc
        if not report:
            raise HealthAnalysisError("AIHubMix returned an empty report")
        LOGGER.info("Report generated")
        return report
