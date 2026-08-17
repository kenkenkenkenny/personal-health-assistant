from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from health_assistant.config import Config
from health_assistant.health_analyzer import (
    HealthAnalysisError,
    HealthAnalyzer,
    calculate_analysis_payload,
)
from health_assistant.models import DailyHealthSummary


def test_calculates_seven_day_windows_and_trends() -> None:
    target = date(2026, 8, 16)
    history = [
        DailyHealthSummary(date=target - timedelta(days=offset), steps=100 - offset * 5)
        for offset in range(14)
    ]
    payload = calculate_analysis_payload(history[0], history)

    assert payload["7_day_stats"]["steps"]["count"] == 7
    assert payload["previous_7_day_stats"]["steps"]["count"] == 7
    assert payload["trends"]["steps"] == "up"
    assert set(payload) == {"today", "7_day_stats", "previous_7_day_stats", "trends"}


def test_aihubmix_uses_responses_api_and_only_summary_payload() -> None:
    client = Mock()
    client.responses.create.return_value.output_text = "健康报告"
    config = Config(AIHUBMIX_API_KEY="test-key", _env_file=None)
    analyzer = HealthAnalyzer(config, client=client)
    payload = {"today": {}, "7_day_stats": {}, "previous_7_day_stats": {}, "trends": {}}

    assert analyzer.analyze(payload) == "健康报告"
    request = client.responses.create.call_args.kwargs
    assert request["model"] == "gpt-5"
    assert "raw_data" not in request["input"]


def test_aihubmix_failure_is_wrapped() -> None:
    client = Mock()
    client.responses.create.side_effect = RuntimeError("provider error")
    analyzer = HealthAnalyzer(
        Config(AIHUBMIX_API_KEY="test-key", _env_file=None), client=client
    )
    with pytest.raises(HealthAnalysisError, match="generation failed"):
        analyzer.analyze({})
