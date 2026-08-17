"""Logging helpers that redact credentials and OAuth tokens."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any


_SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "authorization",
    "openai_api_key",
    "aihubmix_api_key",
    "discord_webhook_url",
    "smtp_password",
}
_TOKEN_PATTERN = re.compile(
    r"(?i)(access_token|refresh_token|client_secret|authorization|api[_-]?key)"
    r"([\s\"'=:\\]+)([^\s,}\"]+)"
)


def sanitize_log(value: Any) -> Any:
    """Return a redacted copy suitable for logs."""
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else sanitize_log(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_log(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_log(item) for item in value)
    if isinstance(value, str):
        return _TOKEN_PATTERN.sub(r"\1\2[REDACTED]", value)
    return value


def configure_logging(verbose: bool = False) -> None:
    """Configure concise console logging without HTTP debug dumps."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("google.auth.transport.requests").setLevel(logging.WARNING)
