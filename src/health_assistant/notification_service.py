"""Discord and SMTP report delivery adapters."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

import requests

from .config import Config


LOGGER = logging.getLogger(__name__)


class NotificationError(RuntimeError):
    """Raised when a configured delivery channel fails."""


class Notifier(Protocol):
    def send(self, subject: str, message: str) -> None: ...


class NullNotifier:
    def send(self, subject: str, message: str) -> None:
        LOGGER.info("Notification disabled; report was not sent")


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.webhook_url = webhook_url
        self.session = session or requests.Session()
        self.timeout = timeout

    def send(self, subject: str, message: str) -> None:
        content = f"**{subject}**\n\n{message}"
        if len(content) > 2000:
            content = content[:1997] + "..."
        try:
            response = self.session.post(
                self.webhook_url,
                json={
                    "content": content,
                    "allowed_mentions": {"parse": []},
                    "username": "Health Assistant",
                },
                timeout=self.timeout,
            )
            if not response.ok:
                raise NotificationError(
                    f"Discord webhook failed (HTTP {response.status_code})"
                )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise NotificationError("Discord webhook network request failed") from exc
        LOGGER.info("Report sent to Discord")


class EmailNotifier:
    def __init__(self, config: Config) -> None:
        self.config = config

    def send(self, subject: str, message: str) -> None:
        email = EmailMessage()
        email["Subject"] = subject
        email["From"] = self.config.smtp_from
        email["To"] = self.config.smtp_to
        email.set_content(message)
        context = ssl.create_default_context()
        try:
            if self.config.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    self.config.smtp_host, self.config.smtp_port, timeout=20, context=context
                ) as server:
                    self._login_and_send(server, email)
            else:
                with smtplib.SMTP(
                    self.config.smtp_host, self.config.smtp_port, timeout=20
                ) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    self._login_and_send(server, email)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotificationError("Email delivery failed") from exc
        LOGGER.info("Report sent by email")

    def _login_and_send(self, server: smtplib.SMTP, email: EmailMessage) -> None:
        if self.config.smtp_username:
            server.login(self.config.smtp_username, self.config.smtp_password)
        server.send_message(email)


def build_notifier(config: Config) -> Notifier:
    config.validate_notification()
    if config.notification_channel == "discord":
        return DiscordNotifier(config.discord_webhook_url)
    if config.notification_channel == "email":
        return EmailNotifier(config)
    return NullNotifier()
