"""Google OAuth 2.0 authorization and credential refresh."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .config import Config


LOGGER = logging.getLogger(__name__)

GOOGLE_HEALTH_SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
)


class GoogleAuthError(RuntimeError):
    """Raised when Google authorization cannot be completed."""


class GoogleAuthService:
    """Own the OAuth flow and local credential persistence."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._flow: Flow | None = None
        self.authorization_state: str | None = None

    def _new_flow(self) -> Flow:
        self.config.validate_google_oauth()
        client_config = {
            "web": {
                "client_id": self.config.google_client_id,
                "client_secret": self.config.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.config.google_redirect_uri],
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=list(GOOGLE_HEALTH_SCOPES),
            redirect_uri=self.config.google_redirect_uri,
        )

    def get_authorization_url(self) -> str:
        """Create an offline OAuth URL and retain state for the callback."""
        self._flow = self._new_flow()
        authorization_url, state = self._flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        self.authorization_state = state
        return authorization_url

    def handle_callback(self, code: str) -> Credentials:
        """Exchange an authorization code and persist the resulting credentials."""
        if not code:
            raise GoogleAuthError("Google callback did not include an authorization code")
        if self._flow is None:
            self._flow = self._new_flow()
        try:
            self._flow.fetch_token(code=code)
        except Exception as exc:
            raise GoogleAuthError("Could not exchange the Google authorization code") from exc

        credentials = self._flow.credentials
        if not credentials.refresh_token:
            raise GoogleAuthError(
                "Google did not return a refresh token; revoke the app grant and authorize again"
            )
        self._save_credentials(credentials)
        LOGGER.info("Google authorization completed; credentials saved locally")
        return credentials

    def get_credentials(self) -> Credentials:
        """Load credentials and refresh an expired access token automatically."""
        token_path = self.config.google_token_path
        self._seed_credentials_if_needed()
        if not token_path.exists():
            raise GoogleAuthError("No local Google credentials found; run the auth command first")
        try:
            credentials = Credentials.from_authorized_user_file(
                str(token_path), scopes=list(GOOGLE_HEALTH_SCOPES)
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise GoogleAuthError("Local Google credentials are invalid or unreadable") from exc

        if credentials.expired or not credentials.valid:
            credentials = self.refresh_credentials(credentials)
        return credentials

    def refresh_credentials(self, credentials: Credentials | None = None) -> Credentials:
        """Refresh and persist Google credentials."""
        if credentials is None:
            try:
                credentials = Credentials.from_authorized_user_file(
                    str(self.config.google_token_path), scopes=list(GOOGLE_HEALTH_SCOPES)
                )
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                raise GoogleAuthError("Cannot load Google credentials for refresh") from exc
        if not credentials.refresh_token:
            raise GoogleAuthError("No Google refresh token is available; run auth again")
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            raise GoogleAuthError("Google credential refresh failed; run auth again") from exc
        self._save_credentials(credentials)
        LOGGER.info("Google access credentials refreshed")
        return credentials

    def _seed_credentials_if_needed(self) -> None:
        """Copy a read-only deployment secret to persistent storage once."""
        token_path = self.config.google_token_path
        seed_path = self.config.google_token_seed_path
        if token_path.exists() or seed_path is None or not seed_path.is_file():
            return

        try:
            seed_contents = seed_path.read_text(encoding="utf-8")
            # Validate before persisting so a malformed cloud secret fails safely.
            Credentials.from_authorized_user_info(
                json.loads(seed_contents), scopes=list(GOOGLE_HEALTH_SCOPES)
            )
            token_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = token_path.with_suffix(token_path.suffix + ".seed.tmp")
            temporary_path.write_text(seed_contents, encoding="utf-8")
            os.chmod(temporary_path, 0o600)
            temporary_path.replace(token_path)
            os.chmod(token_path, 0o600)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise GoogleAuthError("Google credential seed is invalid or unreadable") from exc
        LOGGER.info("Google credentials initialized in persistent storage")

    def _save_credentials(self, credentials: Credentials) -> None:
        token_path: Path = self.config.google_token_path
        token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = token_path.with_suffix(token_path.suffix + ".tmp")
        temporary_path.write_text(credentials.to_json(), encoding="utf-8")
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(token_path)
        os.chmod(token_path, 0o600)
