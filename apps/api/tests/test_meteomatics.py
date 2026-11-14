"""Unit tests for the credentialed Meteomatics weather connector.

HTTP is mocked here so tests never spend account quota. The connector itself was exercised
against the live API during integration verification; results are recorded in
``docs/verification.md``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from terrax.catalog import CATALOG_VERSION, dataset_map
from terrax.connectors import meteomatics
from terrax.connectors.http import SourceUnavailable

USERNAME = "unit-test-user"
PASSWORD = "unit-test-password"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.fixture(autouse=True)
def clear_cache():
    meteomatics.clear_cache()
    yield
    meteomatics.clear_cache()


@pytest.fixture
def configured(monkeypatch):
    fake = SimpleNamespace(
        meteomatics_username=USERNAME,
        meteomatics_password=PASSWORD,
        meteomatics_available=True,
    )
    monkeypatch.setattr(meteomatics, "get_settings", lambda: fake)
    return fake


def settings_without_credentials(monkeypatch):
    fake = SimpleNamespace(
        meteomatics_username=None,
        meteomatics_password=None,
        meteomatics_available=False,
    )
    monkeypatch.setattr(meteomatics, "get_settings", lambda: fake)
    return fake


def forecast_payload() -> dict:
    samples = {
        "t_2m:C": [10.0, 14.0, 18.0],
        "wind_speed_10m:ms": [2.0, 4.0, 10.0],
        "wind_dir_10m:d": [350.0, 10.0, 0.0],
        "relative_humidity_2m:p": [30.0, 40.0, 50.0],
        "precip_1h:mm": [0.0, 1.5, 0.5],
        "total_cloud_cover:p": [20.0, 60.0, 100.0],
    }
    times = [
        "2026-11-14T08:00:00Z",
        "2026-11-14T11:00:00Z",
        "2026-11-14T14:00:00Z",
    ]
    return {
        "version": "3.0",
        "user": USERNAME,
        "dateGenerated": "2026-11-14T07:00:00Z",
        "status": "OK",
        "data": [
            {
                "parameter": parameter,
                "coordinates": [
                    {
                        "lat": -25.3,
                        "lon": -69.9,
                        "dates": [
                            {"date": time, "value": value}
                            for time, value in zip(times, values, strict=True)
                        ],
                    }
                ],
            }
            for parameter, values in samples.items()
        ],
    }


def test_query_builds_documented_path_and_basic_auth(configured, monkeypatch):
    client = FakeClient(FakeResponse(payload=forecast_payload()))
    monkeypatch.setattr(meteomatics, "client", lambda: client)

    payload = meteomatics.query(
        "2026-11-14T12:00:00Z",
        ["t_2m:C", "wind_speed_10m:ms"],
        [(47.3, 8.5), (48.1, 9.2)],
        "json",
        optionals={"tz": "UTC"},
    )

    assert payload["status"] == "OK"
    url, kwargs = client.calls[0]
    assert url == (
        "https://api.meteomatics.com/2026-11-14T12:00:00Z/"
        "t_2m:C,wind_speed_10m:ms/47.30000,8.50000+48.10000,9.20000/json"
    )
    assert kwargs["auth"] == (USERNAME, PASSWORD)
    assert kwargs["params"] == {"tz": "UTC"}


def test_forecast_summarises_and_never_persists_account_identity(configured, monkeypatch):
    client = FakeClient(FakeResponse(payload=forecast_payload()))
    monkeypatch.setattr(meteomatics, "client", lambda: client)

    result = meteomatics.forecast(
        -25.3,
        -69.9,
        start=datetime(2026, 11, 14, 8, tzinfo=UTC),
        hours=6,
        step_hours=3,
        use_cache=False,
    )

    assert result["window"]["start"] == "2026-11-14T08:00:00Z"
    assert result["window"]["end"] == "2026-11-14T14:00:00Z"
    assert result["window"]["interval_hours"] == 3
    summary = result["summary"]
    assert summary["sample_count"] == 3
    assert summary["temperature_mean_c"] == pytest.approx(14.0)
    assert summary["temperature_min_c"] == pytest.approx(10.0)
    assert summary["temperature_max_c"] == pytest.approx(18.0)
    assert summary["wind_speed_mean_ms"] == pytest.approx(5.333, rel=1e-3)
    assert summary["wind_speed_max_ms"] == pytest.approx(10.0)
    assert summary["wind_direction_mean_deg"] == pytest.approx(0.0)
    assert summary["relative_humidity_mean_pct"] == pytest.approx(40.0)
    assert summary["precipitation_max_1h_mm"] == pytest.approx(1.5)
    assert summary["precipitation_sum_sampled_mm"] == pytest.approx(2.0)
    assert summary["cloud_cover_mean_pct"] == pytest.approx(60.0)
    assert summary["parameters_returned"] == sorted(summary["parameters_requested"])

    serialised = json.dumps(result)
    assert USERNAME not in serialised
    assert PASSWORD not in serialised
    assert "unit-test" not in serialised


def test_forecast_is_cached_for_repeated_candidates(configured, monkeypatch):
    client = FakeClient(FakeResponse(payload=forecast_payload()))
    monkeypatch.setattr(meteomatics, "client", lambda: client)

    start = datetime(2026, 11, 14, 8, tzinfo=UTC)
    first = meteomatics.forecast(-25.3, -69.9, start=start)
    second = meteomatics.forecast(-25.3, -69.9, start=start)

    assert first == second
    assert len(client.calls) == 1


def test_query_rejects_credentials_without_leaking_them(configured, monkeypatch):
    client = FakeClient(FakeResponse(status_code=401, payload={"status": "ERROR"}))
    monkeypatch.setattr(meteomatics, "client", lambda: client)

    with pytest.raises(SourceUnavailable) as excinfo:
        meteomatics.query("2026-11-14T12:00:00Z", "t_2m:C", "47.3,8.5", "json")

    message = str(excinfo.value)
    assert "rejected" in message
    assert PASSWORD not in message
    assert USERNAME not in message


def test_query_surfaces_api_parameter_errors_without_identity(configured, monkeypatch):
    error_payload = {
        "version": "3.0",
        "user": USERNAME,
        "status": "ERROR",
        "message": "Parameter cloud_cover:p not available in model mix",
        "error code": "404",
    }
    client = FakeClient(FakeResponse(status_code=404, payload=error_payload))
    monkeypatch.setattr(meteomatics, "client", lambda: client)

    with pytest.raises(SourceUnavailable) as excinfo:
        meteomatics.query("2026-11-14T12:00:00Z", "cloud_cover:p", "47.3,8.5", "json")

    message = str(excinfo.value)
    assert "not available in model mix" in message
    assert PASSWORD not in message
    assert USERNAME not in message


def test_probe_requires_credentials(monkeypatch):
    settings_without_credentials(monkeypatch)

    status, detail, http_status = meteomatics.probe()

    assert status == "requires_credentials"
    assert "not configured" in detail
    assert http_status is None


def test_probe_available_with_valid_credentials(configured, monkeypatch):
    client = FakeClient(FakeResponse(payload=forecast_payload()))
    monkeypatch.setattr(meteomatics, "client", lambda: client)

    status, detail, http_status = meteomatics.probe()

    assert status == "available"
    assert "HTTP 200" in detail
    assert http_status == 200
    assert client.calls


def test_catalog_registers_meteomatics_as_context_only():
    spec = dataset_map()["meteomatics_weather"]
    assert CATALOG_VERSION == "0.4.0"
    assert spec["body_id"] == "earth"
    assert spec["requires_credentials"] is True
    assert spec["is_scoring_input"] is False
    assert "forecast" in spec["title"].lower()
