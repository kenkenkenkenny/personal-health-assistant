"""Environment-backed application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Config(BaseSettings):
    """Settings loaded from environment variables or the project .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8080/oauth/callback"
    google_token_path: Path = Field(default=PROJECT_ROOT / "token.json", exclude=True)
    google_token_seed_path: Path | None = Field(default=None, exclude=True)

    aihubmix_api_key: str = ""
    aihubmix_base_url: str = "https://aihubmix.com/v1"
    aihubmix_model: str = "gpt-5"

    database_url: str = "sqlite:///data/health.db"
    timezone: str = "Europe/London"
    report_time: str = "08:00"
    notification_channel: str = "none"
    discord_webhook_url: str = Field(default="", exclude=True)

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = Field(default="", exclude=True)
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_use_ssl: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("report_time")
    @classmethod
    def validate_report_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("REPORT_TIME must use HH:MM format")
        hour, minute = (int(part) for part in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("REPORT_TIME must be a valid 24-hour time")
        return value

    def validate_google_oauth(self) -> None:
        """Fail early with a useful message before starting OAuth."""
        missing = [
            name
            for name, value in (
                ("GOOGLE_CLIENT_ID", self.google_client_id),
                ("GOOGLE_CLIENT_SECRET", self.google_client_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required setting(s): {', '.join(missing)}")

        parsed = urlparse(self.google_redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("GOOGLE_REDIRECT_URI must be an http://localhost callback URL")

    @field_validator("notification_channel")
    @classmethod
    def validate_notification_channel(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"none", "discord", "email"}:
            raise ValueError("NOTIFICATION_CHANNEL must be none, discord, or email")
        return normalized

    def validate_aihubmix(self) -> None:
        if not self.aihubmix_api_key:
            raise ValueError("Missing required setting: AIHUBMIX_API_KEY")
        parsed = urlparse(self.aihubmix_base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("AIHUBMIX_BASE_URL must be a valid HTTPS URL")

    def validate_notification(self) -> None:
        if self.notification_channel == "discord" and not self.discord_webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL is required for Discord notifications")
        if self.notification_channel == "email":
            required = {
                "SMTP_HOST": self.smtp_host,
                "SMTP_FROM": self.smtp_from,
                "SMTP_TO": self.smtp_to,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing email setting(s): {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return a cached configuration instance."""
    return Config()
