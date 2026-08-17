from __future__ import annotations

from unittest.mock import Mock

import requests

from health_assistant.notification_service import DiscordNotifier


def test_discord_notification_disables_mentions() -> None:
    session = Mock(spec=requests.Session)
    session.post.return_value.ok = True
    notifier = DiscordNotifier("https://discord.example/webhook", session=session)

    notifier.send("健康报告", "一切正常")

    payload = session.post.call_args.kwargs["json"]
    assert payload["allowed_mentions"] == {"parse": []}
    assert "一切正常" in payload["content"]
