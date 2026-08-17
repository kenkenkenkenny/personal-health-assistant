from __future__ import annotations

import pytest
from pydantic import ValidationError

from health_assistant.config import Config


def test_config_rejects_invalid_report_time() -> None:
    with pytest.raises(ValidationError):
        Config(REPORT_TIME="25:00", _env_file=None)


def test_google_settings_fail_early_when_missing() -> None:
    config = Config(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", _env_file=None)
    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
        config.validate_google_oauth()

