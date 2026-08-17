"""SQLAlchemy persistence for normalized daily health summaries."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Date, DateTime, Float, Integer, JSON, create_engine, inspect, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import DailyHealthSummary


class DatabaseError(RuntimeError):
    """Raised when persistence fails."""


class Base(DeclarativeBase):
    pass


class DailyHealthRecord(Base):
    __tablename__ = "daily_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    steps: Mapped[int | None] = mapped_column(Integer)
    sleep_minutes: Mapped[int | None] = mapped_column(Integer)
    sleep_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sleep_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sleep_deep_minutes: Mapped[int | None] = mapped_column(Integer)
    sleep_rem_minutes: Mapped[int | None] = mapped_column(Integer)
    sleep_light_minutes: Mapped[int | None] = mapped_column(Integer)
    sleep_awake_minutes: Mapped[int | None] = mapped_column(Integer)
    resting_heart_rate: Mapped[float | None] = mapped_column(Float)
    heart_rate_average: Mapped[float | None] = mapped_column(Float)
    heart_rate_minimum: Mapped[float | None] = mapped_column(Float)
    heart_rate_maximum: Mapped[float | None] = mapped_column(Float)
    hrv_ms: Mapped[float | None] = mapped_column(Float)
    calories: Mapped[float | None] = mapped_column(Float)
    active_minutes: Mapped[int | None] = mapped_column(Integer)
    exercise_minutes: Mapped[int | None] = mapped_column(Integer)
    distance_km: Mapped[float | None] = mapped_column(Float)
    floors: Mapped[int | None] = mapped_column(Integer)
    active_zone_minutes: Mapped[int | None] = mapped_column(Integer)
    oxygen_saturation_percent: Mapped[float | None] = mapped_column(Float)
    respiratory_rate: Mapped[float | None] = mapped_column(Float)
    vo2_max: Mapped[float | None] = mapped_column(Float)
    raw_data_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HealthDatabase:
    """Own database creation, upsert, and read queries."""

    def __init__(self, database_url: str) -> None:
        if database_url.startswith("sqlite:///") and database_url != "sqlite:///:memory:":
            Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs: dict[str, Any] = {}
        if database_url == "sqlite:///:memory:":
            engine_kwargs = {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        self.engine = create_engine(database_url, **engine_kwargs)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        self._add_missing_sqlite_columns()

    def _add_missing_sqlite_columns(self) -> None:
        """Apply the additive MVP schema migration to existing SQLite files."""
        if self.engine.dialect.name != "sqlite":
            return
        existing = {column["name"] for column in inspect(self.engine).get_columns("daily_health")}
        columns = {
            "sleep_deep_minutes": "INTEGER",
            "sleep_rem_minutes": "INTEGER",
            "sleep_light_minutes": "INTEGER",
            "sleep_awake_minutes": "INTEGER",
            "heart_rate_average": "FLOAT",
            "heart_rate_minimum": "FLOAT",
            "heart_rate_maximum": "FLOAT",
            "distance_km": "FLOAT",
            "floors": "INTEGER",
            "active_zone_minutes": "INTEGER",
            "oxygen_saturation_percent": "FLOAT",
            "respiratory_rate": "FLOAT",
            "vo2_max": "FLOAT",
        }
        with self.engine.begin() as connection:
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.exec_driver_sql(
                        f'ALTER TABLE daily_health ADD COLUMN "{name}" {sql_type}'
                    )

    def save_daily_health(self, summary: DailyHealthSummary) -> DailyHealthSummary:
        now = datetime.now(timezone.utc)
        values = summary.model_dump(exclude={"data_quality"})
        values["raw_data_json"] = {"data_quality": summary.data_quality}
        values["updated_at"] = now
        statement = sqlite_insert(DailyHealthRecord).values(**values, created_at=now)
        statement = statement.on_conflict_do_update(
            index_elements=[DailyHealthRecord.date],
            set_={
                key: getattr(statement.excluded, key)
                for key in values
                if key != "date"
            },
        )
        try:
            with self._sessions.begin() as session:
                session.execute(statement)
        except Exception as exc:
            raise DatabaseError("Could not save daily health data") from exc
        return summary

    def get_daily_health(self, target_date: date) -> DailyHealthSummary | None:
        try:
            with self._sessions() as session:
                record = session.scalar(
                    select(DailyHealthRecord).where(DailyHealthRecord.date == target_date)
                )
                return self._to_summary(record) if record else None
        except Exception as exc:
            raise DatabaseError("Could not read daily health data") from exc

    def get_health_range(self, start_date: date, end_date: date) -> list[DailyHealthSummary]:
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")
        try:
            with self._sessions() as session:
                records = session.scalars(
                    select(DailyHealthRecord)
                    .where(DailyHealthRecord.date.between(start_date, end_date))
                    .order_by(DailyHealthRecord.date)
                ).all()
                return [self._to_summary(record) for record in records]
        except Exception as exc:
            raise DatabaseError("Could not read health history") from exc

    def get_last_n_days(
        self, days: int, *, end_date: date | None = None
    ) -> list[DailyHealthSummary]:
        if days <= 0:
            raise ValueError("days must be positive")
        effective_end = end_date or date.today()
        return self.get_health_range(
            effective_end - timedelta(days=days - 1), effective_end
        )

    @staticmethod
    def _to_summary(record: DailyHealthRecord) -> DailyHealthSummary:
        raw = record.raw_data_json or {}
        return DailyHealthSummary(
            date=record.date,
            steps=record.steps,
            sleep_minutes=record.sleep_minutes,
            sleep_start=record.sleep_start,
            sleep_end=record.sleep_end,
            sleep_deep_minutes=record.sleep_deep_minutes,
            sleep_rem_minutes=record.sleep_rem_minutes,
            sleep_light_minutes=record.sleep_light_minutes,
            sleep_awake_minutes=record.sleep_awake_minutes,
            resting_heart_rate=record.resting_heart_rate,
            heart_rate_average=record.heart_rate_average,
            heart_rate_minimum=record.heart_rate_minimum,
            heart_rate_maximum=record.heart_rate_maximum,
            hrv_ms=record.hrv_ms,
            calories=record.calories,
            active_minutes=record.active_minutes,
            exercise_minutes=record.exercise_minutes,
            distance_km=record.distance_km,
            floors=record.floors,
            active_zone_minutes=record.active_zone_minutes,
            oxygen_saturation_percent=record.oxygen_saturation_percent,
            respiratory_rate=record.respiratory_rate,
            vo2_max=record.vo2_max,
            data_quality=raw.get("data_quality", {}),
        )
