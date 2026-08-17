"""End-to-end daily sync, report generation, and delivery workflow."""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import Config
from .database import HealthDatabase
from .health_analyzer import HealthAnalysisError, HealthAnalyzer, calculate_analysis_payload
from .health_service import HealthService
from .models import DailyHealthSummary
from .notification_service import Notifier


LOGGER = logging.getLogger(__name__)


class ReportService:
    def __init__(
        self,
        config: Config,
        health_service: HealthService,
        database: HealthDatabase,
        analyzer: HealthAnalyzer,
        notifier: Notifier,
    ) -> None:
        self.config = config
        self.health_service = health_service
        self.database = database
        self.analyzer = analyzer
        self.notifier = notifier

    def sync_daily_health(self, target_date: date) -> DailyHealthSummary:
        summary = self.health_service.get_daily_health(target_date)
        self.database.save_daily_health(summary)
        LOGGER.info("Health data saved")
        return summary

    def generate_report(self, target_date: date) -> str:
        today = self.database.get_daily_health(target_date)
        if today is None:
            raise ValueError(f"No saved health data for {target_date}; run sync first")
        history = self.database.get_last_n_days(14, end_date=target_date)
        payload = calculate_analysis_payload(today, history)
        try:
            return self.analyzer.analyze(payload)
        except HealthAnalysisError:
            LOGGER.warning("AI analysis failed; using a deterministic fallback report")
            return self._fallback_report(today)

    def run_daily_health_report(
        self, target_date: date | None = None, *, notify: bool = True
    ) -> str:
        effective_date = target_date or (
            datetime.now(ZoneInfo(self.config.timezone)).date()
        )
        self.sync_daily_health(effective_date)
        report = self.generate_report(effective_date)
        if notify:
            self.notifier.send(f"{effective_date.isoformat()} 每日健康报告", report)
        return report

    @staticmethod
    def _fallback_report(today: DailyHealthSummary) -> str:
        def value(item: object | None, suffix: str = "") -> str:
            return f"{item}{suffix}" if item is not None else "暂无数据"

        sleep = (
            f"{today.sleep_minutes // 60}小时{today.sleep_minutes % 60}分钟"
            if today.sleep_minutes is not None
            else "暂无数据"
        )
        return (
            f"📅 {today.date.isoformat()} 健康报告\n\n"
            f"🏃 活动\n步数：{value(today.steps, '步')}；活跃时间："
            f"{value(today.active_minutes, '分钟')}；运动：{value(today.exercise_minutes, '分钟')}；"
            f"距离：{value(today.distance_km, '公里')}；楼层：{value(today.floors, '层')}；"
            f"活跃区间：{value(today.active_zone_minutes, '分钟')}。\n\n"
            f"😴 睡眠\n昨晚睡眠：{sleep}；深睡：{value(today.sleep_deep_minutes, '分钟')}；"
            f"REM：{value(today.sleep_rem_minutes, '分钟')}；浅睡："
            f"{value(today.sleep_light_minutes, '分钟')}；清醒："
            f"{value(today.sleep_awake_minutes, '分钟')}。\n\n"
            f"❤️ 心率\n静息：{value(today.resting_heart_rate, ' bpm')}；全天平均/最低/最高："
            f"{value(today.heart_rate_average, ' bpm')} / {value(today.heart_rate_minimum, ' bpm')} / "
            f"{value(today.heart_rate_maximum, ' bpm')}。\n\n"
            f"💓 HRV\nHRV：{value(today.hrv_ms, ' ms')}。\n\n"
            f"🫁 恢复\n血氧：{value(today.oxygen_saturation_percent, '%')}；呼吸率："
            f"{value(today.respiratory_rate, '次/分钟')}；VO₂ Max：{value(today.vo2_max)}。\n\n"
            "📊 总体趋势\nAI 分析暂时不可用，本报告仅展示已同步数据，不作疾病诊断。\n\n"
            "💡 今日建议\n保持规律作息，结合自身感受安排适量活动；缺失数据可在设备同步后重试。"
        )
