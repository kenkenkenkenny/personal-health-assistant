"""Command-line entry point for sync, reporting, delivery, and scheduling."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .config import Config, get_config
from .database import DatabaseError, HealthDatabase
from .google_auth import GoogleAuthError, GoogleAuthService
from .google_health import GoogleHealthClient, GoogleHealthError
from .health_analyzer import HealthAnalysisError, HealthAnalyzer
from .health_service import HealthService
from .logging_utils import configure_logging
from .notification_service import NotificationError, build_notifier
from .report_service import ReportService
from .scheduler import run_scheduler


LOGGER = logging.getLogger(__name__)


def _run_auth(config: Config) -> None:
    auth_service = GoogleAuthService(config)
    authorization_url = auth_service.get_authorization_url()
    callback = urlparse(config.google_redirect_uri)
    if callback.hostname not in {"localhost", "127.0.0.1"} or callback.port is None:
        raise ValueError("GOOGLE_REDIRECT_URI must include localhost and an explicit port")

    expected_path = callback.path or "/"
    result: dict[str, str] = {}
    completed = Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path != expected_path:
                self.send_error(404)
                return
            if "error" in query:
                result["error"] = query["error"][0]
            elif "code" in query:
                returned_state = query.get("state", [""])[0]
                expected_state = auth_service.authorization_state or ""
                if not expected_state or not hmac.compare_digest(returned_state, expected_state):
                    result["error"] = "invalid_oauth_state"
                else:
                    result["code"] = query["code"][0]
            else:
                result["error"] = "missing_code"
            body = "Authorization received. You can close this tab.".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            completed.set()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer((callback.hostname, callback.port), CallbackHandler)
    server.timeout = 1
    print("Open this URL in your browser:\n")
    print(authorization_url)
    print(f"\nWaiting for Google to redirect to {config.google_redirect_uri} ...")
    try:
        while not completed.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        print("\nAuthorization cancelled.")
        return
    finally:
        server.server_close()

    if "error" in result:
        raise GoogleAuthError(f"Google authorization was not completed: {result['error']}")
    auth_service.handle_callback(result["code"])
    print("Authorization successful. token.json was saved with owner-only permissions.")


def _run_steps(config: Config, target_date: date) -> None:
    client = GoogleHealthClient(GoogleAuthService(config))
    LOGGER.info("Fetching steps for %s", target_date.isoformat())
    steps = client.get_steps(target_date)
    if steps is None:
        print(f"{target_date.isoformat()}: no steps data available")
    else:
        print(f"{target_date.isoformat()}: {steps:,} steps")


def _run_check(config: Config, target_date: date) -> None:
    client = GoogleHealthClient(GoogleAuthService(config))
    LOGGER.info("Checking Google Health GET access for %s", target_date.isoformat())
    has_data = client.check_steps_access(target_date)
    suffix = "steps data found" if has_data else "no steps data for this date"
    print(f"Google Health API connection successful: {suffix}")


def _client(config: Config) -> GoogleHealthClient:
    return GoogleHealthClient(GoogleAuthService(config))


def _run_sleep(config: Config, target_date: date) -> None:
    sleep = _client(config).get_sleep(target_date)
    if sleep is None:
        print(f"{target_date.isoformat()}: no sleep data available")
    else:
        print(json.dumps(sleep.model_dump(mode="json"), ensure_ascii=False, indent=2))


def _run_resting_heart_rate(config: Config, target_date: date) -> None:
    value = _client(config).get_resting_heart_rate(target_date)
    print(
        f"{target_date.isoformat()}: "
        + (f"{value:g} bpm" if value is not None else "no resting heart rate data available")
    )


def _run_hrv(config: Config, target_date: date) -> None:
    value = _client(config).get_hrv(target_date)
    print(
        f"{target_date.isoformat()}: "
        + (f"{value:g} ms HRV" if value is not None else "no HRV data available")
    )


def _run_health(config: Config, target_date: date) -> None:
    summary = HealthService(_client(config)).get_daily_health(target_date)
    print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))


def _database(config: Config) -> HealthDatabase:
    database = HealthDatabase(config.database_url)
    database.initialize()
    return database


def _report_service(config: Config) -> ReportService:
    return ReportService(
        config=config,
        health_service=HealthService(_client(config)),
        database=_database(config),
        analyzer=HealthAnalyzer(config),
        notifier=build_notifier(config),
    )


def _run_sync(config: Config, target_date: date) -> None:
    summary = HealthService(_client(config)).get_daily_health(target_date)
    _database(config).save_daily_health(summary)
    print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))


def _run_history(config: Config, days: int) -> None:
    history = _database(config).get_last_n_days(days, end_date=_default_date(config))
    print(
        json.dumps(
            [item.model_dump(mode="json") for item in history],
            ensure_ascii=False,
            indent=2,
        )
    )


def _run_report(config: Config, target_date: date) -> None:
    print(_report_service(config).generate_report(target_date))


def _run_daily(config: Config, target_date: date) -> None:
    print(_report_service(config).run_daily_health_report(target_date, notify=True))


def _default_date(config: Config) -> date:
    return datetime.now(ZoneInfo(config.timezone)).date()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal Google Health assistant")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("auth", help="Authorize Google Health access")
    check_parser = subparsers.add_parser("check", help="Verify Google Health GET access")
    check_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    steps_parser = subparsers.add_parser("steps", help="Fetch one day of steps")
    steps_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    sleep_parser = subparsers.add_parser("sleep", help="Fetch sleep ending on a date")
    sleep_parser.add_argument("--date", type=_parse_date, help="Wake date in YYYY-MM-DD")
    resting_parser = subparsers.add_parser(
        "resting-heart-rate", help="Fetch daily resting heart rate"
    )
    resting_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    hrv_parser = subparsers.add_parser("hrv", help="Fetch daily HRV")
    hrv_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    health_parser = subparsers.add_parser("health", help="Fetch normalized Phase 2 summary")
    health_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    sync_parser = subparsers.add_parser("sync", help="Fetch and upsert one day")
    sync_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    history_parser = subparsers.add_parser("history", help="Show saved health history")
    history_parser.add_argument("--days", type=int, default=7)
    report_parser = subparsers.add_parser("report", help="Generate a saved day's report")
    report_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    daily_parser = subparsers.add_parser("daily", help="Sync, report, and notify")
    daily_parser.add_argument("--date", type=_parse_date, help="Civil date in YYYY-MM-DD")
    subparsers.add_parser("scheduler", help="Run the daily scheduler")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)
    config = get_config()
    try:
        if args.command == "auth":
            _run_auth(config)
        elif args.command == "check":
            _run_check(config, args.date or _default_date(config))
        elif args.command == "steps":
            _run_steps(config, args.date or _default_date(config))
        elif args.command == "sleep":
            _run_sleep(config, args.date or _default_date(config))
        elif args.command == "resting-heart-rate":
            _run_resting_heart_rate(config, args.date or _default_date(config))
        elif args.command == "hrv":
            _run_hrv(config, args.date or _default_date(config))
        elif args.command == "health":
            _run_health(config, args.date or _default_date(config))
        elif args.command == "sync":
            _run_sync(config, args.date or _default_date(config))
        elif args.command == "history":
            _run_history(config, args.days)
        elif args.command == "report":
            _run_report(config, args.date or _default_date(config))
        elif args.command == "daily":
            _run_daily(config, args.date or _default_date(config))
        elif args.command == "scheduler":
            run_scheduler(config, _report_service(config))
    except (
        GoogleAuthError,
        GoogleHealthError,
        DatabaseError,
        HealthAnalysisError,
        NotificationError,
        ValueError,
        OSError,
    ) as exc:
        LOGGER.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
