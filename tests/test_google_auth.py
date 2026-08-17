from __future__ import annotations

import json
import stat
from pathlib import Path

from health_assistant.config import Config
from health_assistant.google_auth import GOOGLE_HEALTH_SCOPES, GoogleAuthService


def _token_payload() -> dict[str, object]:
    return {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": list(GOOGLE_HEALTH_SCOPES),
        "expiry": "2999-01-01T00:00:00Z",
    }


def test_cloud_seed_is_copied_once_with_private_permissions(tmp_path: Path) -> None:
    seed_path = tmp_path / "secret" / "token.json"
    token_path = tmp_path / "persistent" / "token.json"
    seed_path.parent.mkdir()
    seed_path.write_text(json.dumps(_token_payload()), encoding="utf-8")
    config = Config(
        GOOGLE_TOKEN_PATH=token_path,
        GOOGLE_TOKEN_SEED_PATH=seed_path,
        _env_file=None,
    )

    service = GoogleAuthService(config)
    credentials = service.get_credentials()

    assert credentials.refresh_token == "refresh-token"
    assert token_path.exists()
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

    seed_path.write_text("not-json", encoding="utf-8")
    assert service.get_credentials().refresh_token == "refresh-token"
