from __future__ import annotations

from datetime import date
from unittest.mock import Mock

from health_assistant.config import Config
from health_assistant.health_analyzer import HealthAnalysisError
from health_assistant.models import DailyHealthSummary
from health_assistant.report_service import ReportService


def test_daily_workflow_syncs_reports_and_notifies() -> None:
    target = date(2026, 8, 16)
    summary = DailyHealthSummary(date=target, steps=8000)
    health_service = Mock()
    health_service.get_daily_health.return_value = summary
    database = Mock()
    database.get_daily_health.return_value = summary
    database.get_last_n_days.return_value = [summary]
    analyzer = Mock()
    analyzer.analyze.return_value = "AI 健康报告"
    notifier = Mock()
    service = ReportService(
        Config(_env_file=None), health_service, database, analyzer, notifier
    )

    assert service.run_daily_health_report(target) == "AI 健康报告"
    database.save_daily_health.assert_called_once_with(summary)
    notifier.send.assert_called_once()


def test_report_falls_back_when_aihubmix_fails() -> None:
    target = date(2026, 8, 16)
    summary = DailyHealthSummary(date=target, steps=8000)
    database = Mock()
    database.get_daily_health.return_value = summary
    database.get_last_n_days.return_value = [summary]
    analyzer = Mock()
    analyzer.analyze.side_effect = HealthAnalysisError("failed")
    service = ReportService(
        Config(_env_file=None), Mock(), database, analyzer, Mock()
    )

    report = service.generate_report(target)
    assert "AI 分析暂时不可用" in report
    assert "不作疾病诊断" in report
