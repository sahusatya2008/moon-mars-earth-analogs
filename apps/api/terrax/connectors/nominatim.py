"""Nominatim reverse geocoding (OpenStreetMap) with disk cache and 1 req/s policy."""

from __future__ import annotations

import json
import threading
import time

from ..config import get_settings
from .http import get_json

settings = get_settings()

URL = "https://nominatim.openstreetmap.org/reverse"
_lock = threading.Lock()
_last_call = 0.0


def _cache_path(lat: float, lon: float):
    path = settings.cache_dir / "gazetteer" / f"{lat:.3f}_{lon:.3f}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def reverse(lat: float, lon: float) -> dict | None:
    path = _cache_path(lat, lon)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    global _last_call
    with _lock:
        wait = 1.05 - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            data = get_json(
                URL,
                params={
                    "format": "jsonv2",
                    "lat": f"{lat:.6f}",
                    "lon": f"{lon:.6f}",
                    "zoom": 8,
                    "addressdetails": 1,
                    "accept-language": "en",
                },
                headers={"User-Agent": settings.terrax_gazetteer_user_agent},
            )
            path.write_text(json.dumps(data))
            return data
        except Exception:
            return None


def describe(lat: float, lon: float) -> tuple[str, str | None, str | None]:
    data = reverse(lat, lon)
    if not data:
        return f"{abs(lat):.3f}°{'N' if lat >= 0 else 'S'} {abs(lon):.3f}°{'E' if lon >= 0 else 'W'}", None, None
    addr = data.get("address", {})
    name = (
        data.get("name")
        or addr.get("protected_area")
        or addr.get("natural")
        or addr.get("desert")
        or addr.get("locality")
        or data.get("display_name", "").split(",")[0]
    )
    country = addr.get("country")
    region = addr.get("state") or addr.get("county") or addr.get("region")
    if not name:
        name = f"{abs(lat):.3f}°{'N' if lat >= 0 else 'S'} {abs(lon):.3f}°{'E' if lon >= 0 else 'W'}"
    display = ", ".join([p for p in (name, region, country) if p])
    return display, country, region


__all__ = ["reverse", "describe"]
