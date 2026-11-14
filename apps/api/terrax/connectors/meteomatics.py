"""Meteomatics weather and forecast API (optional; credentialed).

The API path follows the documented template::

    /{validdatetime}/{parameters}/{locations}/{format}?{optionals}

Authentication is HTTP Basic. Credentials are read from the environment, used only in
the request authorization header, and never written to evidence, manifests, reports,
logs, or error messages. The API response's ``user`` field is deliberately discarded.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import threading
import time
import urllib.parse
from collections.abc import Sequence
from typing import Any

from ..config import get_settings
from .http import SourceUnavailable, client

BASE = "https://api.meteomatics.com"

# Parameters verified against the hackathon account's model mix. ``cloud_cover:p`` is not
# available; ``total_cloud_cover:p`` is. Keeping this list explicit prevents a single
# unsupported parameter from failing the whole point forecast.
DEFAULT_PARAMETERS = (
    "t_2m:C",
    "wind_speed_10m:ms",
    "wind_dir_10m:d",
    "relative_humidity_2m:p",
    "precip_1h:mm",
    "total_cloud_cover:p",
)

PARAMETER_UNITS = {
    "t_2m:C": "degC",
    "wind_speed_10m:ms": "m/s",
    "wind_dir_10m:d": "degrees",
    "relative_humidity_2m:p": "percent",
    "precip_1h:mm": "mm",
    "total_cloud_cover:p": "percent",
}

FORECAST_HOURS = 48
FORECAST_STEP_HOURS = 3
CACHE_TTL_S = 600.0

_forecast_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _credentials() -> tuple[str, str]:
    settings = get_settings()
    if not settings.meteomatics_available:
        raise SourceUnavailable(
            "Meteomatics requires METEOMATICS_USERNAME and METEOMATICS_PASSWORD."
        )
    return settings.meteomatics_username or "", settings.meteomatics_password or ""


def _redact(value: Any) -> str:
    """Strip the configured account credentials from any text before it can be stored."""
    settings = get_settings()
    text = str(value)
    for secret in (
        settings.meteomatics_password,
        settings.meteomatics_username,
        urllib.parse.quote(settings.meteomatics_password or "", safe=""),
        urllib.parse.quote(settings.meteomatics_username or "", safe=""),
    ):
        if secret:
            text = text.replace(secret, "<meteomatics-credential>")
    return text[:600]


def _locations_text(locations: str | Sequence[Any]) -> str:
    if isinstance(locations, str):
        return locations
    parts: list[str] = []
    for location in locations:
        if isinstance(location, str):
            parts.append(location)
            continue
        lat, lon = location
        parts.append(f"{float(lat):.5f},{float(lon):.5f}")
    return "+".join(parts)


def _parameters_text(parameters: str | Sequence[str]) -> str:
    return parameters if isinstance(parameters, str) else ",".join(parameters)


def _error_message(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload)[:300]
    return str(payload)[:300]


def query(
    valid_datetime: str,
    parameters: str | Sequence[str],
    locations: str | Sequence[Any],
    fmt: str = "json",
    *,
    optionals: dict[str, Any] | None = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Call the documented ``validdatetime/parameters/locations/format`` endpoint.

    ``valid_datetime`` accepts a single time or a Meteomatics interval such as
    ``2026-11-14T00:00:00Z--2026-11-16T00:00:00Z:PT3H``. ``optionals`` become query
    parameters (for example ``{"source": "ecmwf", "tz": "UTC"}``).
    """
    username, password = _credentials()
    url = (
        f"{BASE}/{valid_datetime}/"
        f"{_parameters_text(parameters)}/{_locations_text(locations)}/{fmt}"
    )
    try:
        response = client().get(
            url,
            auth=(username, password),
            params=optionals,
            timeout=timeout,
        )
    except Exception as exc:
        raise SourceUnavailable(f"Meteomatics request failed: {_redact(exc)}") from None

    if response.status_code in (401, 403):
        raise SourceUnavailable(
            "Meteomatics rejected the configured credentials "
            f"(HTTP {response.status_code})."
        )
    if response.status_code >= 400:
        raise SourceUnavailable(
            f"Meteomatics query failed (HTTP {response.status_code}): "
            f"{_redact(_error_message(response))}"
        )
    try:
        payload = response.json()
    except ValueError:
        raise SourceUnavailable("Meteomatics returned a non-JSON response.") from None
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        message = payload.get("message") if isinstance(payload, dict) else "unexpected payload"
        raise SourceUnavailable(f"Meteomatics query was not OK: {_redact(message)}")
    return payload


def _parse_series(
    payload: dict[str, Any], requested: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    series: dict[str, list[dict[str, Any]]] = {}
    for block in payload.get("data") or []:
        parameter = str(block.get("parameter") or "")
        if parameter not in requested:
            continue
        points: list[dict[str, Any]] = []
        for coordinate in block.get("coordinates") or []:
            for sample in coordinate.get("dates") or []:
                value = sample.get("value")
                if value is None:
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(number):
                    continue
                points.append(
                    {
                        "time": sample.get("date"),
                        "value": round(number, 3),
                    }
                )
        if points:
            points.sort(key=lambda point: str(point.get("time") or ""))
            series[parameter] = points
    return series


def _values(series: dict[str, list[dict[str, Any]]], parameter: str) -> list[float]:
    return [float(point["value"]) for point in series.get(parameter, [])]


def _circular_mean_deg(values: list[float]) -> float | None:
    if not values:
        return None
    radians = [math.radians(value) for value in values]
    sine = sum(math.sin(value) for value in radians) / len(radians)
    cosine = sum(math.cos(value) for value in radians) / len(radians)
    if abs(sine) < 1e-12 and abs(cosine) < 1e-12:
        return None
    return round(math.degrees(math.atan2(sine, cosine)), 2) % 360.0


def _summarise(
    series: dict[str, list[dict[str, Any]]], requested: Sequence[str]
) -> dict[str, Any]:
    temperature = _values(series, "t_2m:C")
    wind_speed = _values(series, "wind_speed_10m:ms")
    wind_direction = _values(series, "wind_dir_10m:d")
    humidity = _values(series, "relative_humidity_2m:p")
    precipitation = _values(series, "precip_1h:mm")
    cloud = _values(series, "total_cloud_cover:p")
    sample_count = max((len(values) for values in series.values()), default=0)
    summary: dict[str, Any] = {
        "sample_count": sample_count,
        "temperature_mean_c": round(sum(temperature) / len(temperature), 2) if temperature else None,
        "temperature_min_c": round(min(temperature), 2) if temperature else None,
        "temperature_max_c": round(max(temperature), 2) if temperature else None,
        "wind_speed_mean_ms": round(sum(wind_speed) / len(wind_speed), 2) if wind_speed else None,
        "wind_speed_max_ms": round(max(wind_speed), 2) if wind_speed else None,
        "wind_direction_mean_deg": _circular_mean_deg(wind_direction),
        "relative_humidity_mean_pct": round(sum(humidity) / len(humidity), 2) if humidity else None,
        "precipitation_max_1h_mm": round(max(precipitation), 2) if precipitation else None,
        "precipitation_sum_sampled_mm": round(sum(precipitation), 2) if precipitation else None,
        "cloud_cover_mean_pct": round(sum(cloud) / len(cloud), 2) if cloud else None,
        "cloud_cover_min_pct": round(min(cloud), 2) if cloud else None,
        "cloud_cover_max_pct": round(max(cloud), 2) if cloud else None,
        "parameters_returned": sorted(series),
        "parameters_requested": list(requested),
    }
    return summary


def forecast(
    lat: float,
    lon: float,
    *,
    start: dt.datetime | None = None,
    hours: int = FORECAST_HOURS,
    step_hours: int = FORECAST_STEP_HOURS,
    parameters: Sequence[str] = DEFAULT_PARAMETERS,
    optionals: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return a bounded point forecast with a compact statistical summary.

    The default window starts at the current UTC hour and spans 48 hours at a 3-hour
    step. Values are model output, not in-situ measurements, and are never used in the
    similarity score.
    """
    if start is None:
        start_dt = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    elif start.tzinfo is None:
        start_dt = start.replace(tzinfo=dt.UTC)
    else:
        start_dt = start.astimezone(dt.UTC)
    start_dt = start_dt.replace(minute=0, second=0, microsecond=0)
    hours = max(6, min(168, int(hours)))
    step_hours = max(1, min(24, int(step_hours)))
    end_dt = start_dt + dt.timedelta(hours=hours)
    valid_datetime = (
        f"{start_dt:%Y-%m-%dT%H:%M:%SZ}--{end_dt:%Y-%m-%dT%H:%M:%SZ}:PT{step_hours}H"
    )

    cache_key = hashlib.sha256(
        json.dumps(
            {
                "valid": valid_datetime,
                "lat": round(float(lat), 5),
                "lon": round(float(lon), 5),
                "parameters": list(parameters),
                "optionals": optionals or {},
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    now = time.monotonic()
    if use_cache:
        with _cache_lock:
            cached = _forecast_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

    payload = query(
        valid_datetime,
        parameters,
        f"{float(lat):.5f},{float(lon):.5f}",
        "json",
        optionals=optionals,
    )
    series = _parse_series(payload, parameters)
    result = {
        "source": "Meteomatics Weather API",
        "location": {"lat": round(float(lat), 5), "lon": round(float(lon), 5)},
        "window": {
            "start": start_dt.isoformat().replace("+00:00", "Z"),
            "end": end_dt.isoformat().replace("+00:00", "Z"),
            "interval_hours": step_hours,
            "timezone": "UTC",
        },
        "summary": _summarise(series, parameters),
        "series": series,
        "units": {
            parameter: PARAMETER_UNITS.get(parameter, "unknown")
            for parameter in parameters
        },
        "model": "Meteomatics model mix (model-dependent; not a single sensor)",
        "caveat": (
            "Point forecast from a numerical weather model blend, not an in-situ "
            "measurement. Provided as operational context only and never used in scoring."
        ),
    }
    if use_cache:
        with _cache_lock:
            _forecast_cache[cache_key] = (now + CACHE_TTL_S, result)
    return result


def probe() -> tuple[str, str, int | None]:
    """Check configuration and make one bounded authenticated forecast request."""
    settings = get_settings()
    if not settings.meteomatics_available:
        return (
            "requires_credentials",
            "METEOMATICS_USERNAME and METEOMATICS_PASSWORD are not configured; "
            "the service is not reported as available.",
            None,
        )
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    valid = f"{now:%Y-%m-%dT%H:%M:%SZ}"
    try:
        query(valid, "t_2m:C", "47.3769,8.5417", "json")
    except SourceUnavailable as exc:
        message = str(exc)
        if "rejected" in message or "HTTP 401" in message or "HTTP 403" in message:
            return "requires_credentials", message, 401
        return "degraded", message, None
    return (
        "available",
        "HTTP 200; authenticated point forecast returned by the Meteomatics model mix.",
        200,
    )


def clear_cache() -> None:
    with _cache_lock:
        _forecast_cache.clear()


__all__ = [
    "BASE",
    "DEFAULT_PARAMETERS",
    "PARAMETER_UNITS",
    "clear_cache",
    "forecast",
    "probe",
    "query",
]
