from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest
import requests
from google.oauth2.credentials import Credentials

from health_assistant.google_health import GoogleHealthClient, GoogleHealthError


def _response(status: int, payload: dict[str, object], **headers: str) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.ok = 200 <= status < 300
    response.headers = headers
    response.json.return_value = payload
    return response


@pytest.fixture
def auth_service() -> Mock:
    service = Mock()
    service.get_credentials.return_value = Credentials(token="not-a-real-token")
    service.refresh_credentials.return_value = Credentials(token="refreshed-not-a-real-token")
    return service


def test_get_steps_parses_official_rollup_shape(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.return_value = _response(
        200, {"rollupDataPoints": [{"steps": {"countSum": "8234"}}]}
    )
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.get_steps(date(2026, 8, 16)) == 8234
    body = session.request.call_args.kwargs["json"]
    assert body["range"]["start"]["date"] == {"year": 2026, "month": 8, "day": 16}
    assert body["range"]["end"]["date"] == {"year": 2026, "month": 8, "day": 17}


def test_get_steps_returns_none_for_missing_data(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.return_value = _response(200, {"rollupDataPoints": [{}]})
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.get_steps(date(2026, 8, 16)) is None


def test_get_sleep_uses_summary_and_keeps_naps_separate(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.return_value = _response(
        200,
        {
            "dataPoints": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2026-08-15T22:30:00Z",
                            "endTime": "2026-08-16T06:30:00Z",
                        },
                        "metadata": {"nap": False},
                        "summary": {
                            "minutesAsleep": "438",
                            "stagesSummary": [
                                {"type": "DEEP", "minutes": "80"},
                                {"type": "REM", "minutes": "90"},
                                {"type": "LIGHT", "minutes": "268"},
                                {"type": "AWAKE", "minutes": "42"},
                                {"type": "DEEP", "minutes": "80"},
                            ],
                        },
                    }
                },
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2026-08-16T13:00:00Z",
                            "endTime": "2026-08-16T13:25:00Z",
                        },
                        "metadata": {"nap": True},
                        "summary": {"minutesAsleep": "20"},
                    }
                },
            ]
        },
    )
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    result = client.get_sleep(date(2026, 8, 16))

    assert result is not None
    assert result.total_sleep_minutes == 438
    assert result.nap_minutes == 20
    assert result.deep_minutes == 80
    assert result.rem_minutes == 90
    assert result.light_minutes == 268
    assert result.awake_minutes == 42
    assert result.sleep_start.isoformat() == "2026-08-15T22:30:00+00:00"
    request = session.request.call_args.kwargs
    assert "sleep.interval.civil_end_time" in request["params"]["filter"]


def test_get_sleep_falls_back_to_sleeping_stage_durations(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.return_value = _response(
        200,
        {
            "dataPoints": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2026-08-15T22:00:00Z",
                            "endTime": "2026-08-15T23:30:00Z",
                        },
                        "stages": [
                            {
                                "type": "LIGHT",
                                "startTime": "2026-08-15T22:00:00Z",
                                "endTime": "2026-08-15T23:00:00Z",
                            },
                            {
                                "type": "AWAKE",
                                "startTime": "2026-08-15T23:00:00Z",
                                "endTime": "2026-08-15T23:30:00Z",
                            },
                        ],
                    }
                }
            ]
        },
    )
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.get_sleep(date(2026, 8, 16)).total_sleep_minutes == 60  # type: ignore[union-attr]


def test_get_resting_heart_rate_parses_reconciled_daily_value(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.return_value = _response(
        200,
        {
            "dataPoints": [
                {
                    "dailyRestingHeartRate": {
                        "date": {"year": 2026, "month": 8, "day": 16},
                        "beatsPerMinute": "61",
                    }
                }
            ]
        },
    )
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.get_resting_heart_rate(date(2026, 8, 16)) == 61.0
    assert "daily_resting_heart_rate.date" in session.request.call_args.kwargs["params"]["filter"]


def test_get_hrv_prefers_daily_value(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.return_value = _response(
        200,
        {
            "dataPoints": [
                {
                    "dailyHeartRateVariability": {
                        "date": {"year": 2026, "month": 8, "day": 16},
                        "averageHeartRateVariabilityMilliseconds": 46.5,
                    }
                }
            ]
        },
    )
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.get_hrv(date(2026, 8, 16)) == 46.5
    assert session.request.call_count == 1


def test_get_hrv_falls_back_to_mean_sample_rmssd(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        _response(200, {"dataPoints": []}),
        _response(
            200,
            {
                "dataPoints": [
                    {
                        "heartRateVariability": {
                            "rootMeanSquareOfSuccessiveDifferencesMilliseconds": 40
                        }
                    },
                    {
                        "heartRateVariability": {
                            "rootMeanSquareOfSuccessiveDifferencesMilliseconds": 50
                        }
                    },
                ]
            },
        ),
    ]
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.get_hrv(date(2026, 8, 16)) == 45.0
    assert "heart_rate_variability.sample_time.civil_time" in (
        session.request.call_args.kwargs["params"]["filter"]
    )


def test_activity_rollups_and_exercise_duration(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        _response(
            200,
            {
                "rollupDataPoints": [
                    {
                        "activeMinutes": {
                            "activeMinutesRollupByActivityLevel": [
                                {"activityLevel": "LIGHT", "activeMinutesSum": "20"},
                                {"activityLevel": "MODERATE", "activeMinutesSum": "15"},
                            ]
                        }
                    }
                ]
            },
        ),
        _response(
            200,
            {"rollupDataPoints": [{"totalCalories": {"kcalSum": 2050.5}}]},
        ),
        _response(
            200,
            {"dataPoints": [{"exercise": {"activeDuration": "1800s"}}]},
        ),
    ]
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())
    target = date(2026, 8, 16)

    assert client.get_active_minutes(target) == 35
    assert client.get_total_calories(target) == 2050.5
    assert client.get_exercise(target) == 30


def test_extended_rollups_parse_distance_floors_zones_and_heart_rate(
    auth_service: Mock,
) -> None:
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        _response(200, {"rollupDataPoints": [{"distance": {"millimetersSum": "5400000"}}]}),
        _response(200, {"rollupDataPoints": [{"floors": {"countSum": "12"}}]}),
        _response(
            200,
            {
                "rollupDataPoints": [
                    {
                        "activeZoneMinutes": {
                            "sumInFatBurnHeartZone": "10",
                            "sumInCardioHeartZone": "8",
                            "sumInPeakHeartZone": "2",
                        }
                    }
                ]
            },
        ),
        _response(
            200,
            {
                "rollupDataPoints": [
                    {
                        "heartRate": {
                            "beatsPerMinuteAvg": 77.5,
                            "beatsPerMinuteMin": 49,
                            "beatsPerMinuteMax": 151,
                        }
                    }
                ]
            },
        ),
    ]
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())
    target = date(2026, 8, 17)

    assert client.get_distance(target) == 5.4
    assert client.get_floors(target) == 12
    assert client.get_active_zone_minutes(target) == 20
    heart = client.get_heart_rate_stats(target)
    assert heart is not None
    assert (heart.average, heart.minimum, heart.maximum) == (77.5, 49, 151)


def test_extended_daily_recovery_metrics(auth_service: Mock) -> None:
    target_date = {"year": 2026, "month": 8, "day": 17}
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        _response(
            200,
            {"dataPoints": [{"dailyOxygenSaturation": {"date": target_date, "averagePercentage": 96.7}}]},
        ),
        _response(
            200,
            {"dataPoints": [{"dailyRespiratoryRate": {"date": target_date, "breathsPerMinute": 15.2}}]},
        ),
        _response(
            200,
            {"dataPoints": [{"dailyVo2Max": {"date": target_date, "vo2Max": 44.1}}]},
        ),
    ]
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())
    target = date(2026, 8, 17)

    assert client.get_oxygen_saturation(target) == 96.7
    assert client.get_respiratory_rate(target) == 15.2
    assert client.get_vo2_max(target) == 44.1


def test_check_steps_access_uses_documented_get_filter(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.return_value = _response(200, {"dataPoints": []})
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.check_steps_access(date(2026, 8, 16)) is False
    request = session.request.call_args.kwargs
    assert request["method"] == "GET"
    assert request["params"] == {
        "filter": 'steps.interval.civil_start_time >= "2026-08-16T00:00:00"',
        "page_size": 1,
    }


def test_401_refreshes_once_then_succeeds(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        _response(401, {}),
        _response(200, {"rollupDataPoints": [{"steps": {"countSum": "10"}}]}),
    ]
    client = GoogleHealthClient(auth_service, session=session, sleep=Mock())

    assert client.get_steps(date(2026, 8, 16)) == 10
    auth_service.refresh_credentials.assert_called_once_with()


def test_429_is_retried(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        _response(429, {}, **{"Retry-After": "0"}),
        _response(200, {"rollupDataPoints": [{"steps": {"countSum": "12"}}]}),
    ]
    sleeper = Mock()
    client = GoogleHealthClient(auth_service, session=session, sleep=sleeper)

    assert client.get_steps(date(2026, 8, 16)) == 12
    sleeper.assert_called_once_with(0.0)


def test_timeout_is_retried_then_fails(auth_service: Mock) -> None:
    session = Mock(spec=requests.Session)
    session.request.side_effect = requests.Timeout("secret-free timeout")
    client = GoogleHealthClient(
        auth_service, session=session, max_retries=1, sleep=Mock()
    )

    with pytest.raises(GoogleHealthError, match="network request failed"):
        client.get_steps(date(2026, 8, 16))
