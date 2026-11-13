"""NASA FIRMS active-fire detections (optional; requires a free MAP_KEY)."""

from __future__ import annotations

import csv
import io

from ..config import get_settings
from .http import SourceUnavailable, get_bytes

settings = get_settings()

BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"


def active_fires(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    days: int = 2,
    source: str = "VIIRS_SNPP_NRT",
) -> dict:
    if not settings.nasa_firms_map_key:
        raise SourceUnavailable(
            "FIRMS requires NASA_FIRMS_MAP_KEY (free registration at firms.modaps.eosdis.nasa.gov)."
        )
    days = max(1, min(5, days))
    url = (
        f"{BASE}/{settings.nasa_firms_map_key}/{source}/"
        f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}/{days}"
    )
    try:
        text = get_bytes(url, timeout=45).decode("utf-8", "replace")
    except Exception as exc:
        raise SourceUnavailable(f"FIRMS query failed: {exc}") from exc
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        try:
            rows.append(
                {
                    "latitude": float(row.get("latitude", "nan")),
                    "longitude": float(row.get("longitude", "nan")),
                    "brightness_k": float(row.get("bright_ti4", "nan")),
                    "confidence": row.get("confidence"),
                    "acq_date": row.get("acq_date"),
                    "acq_time": row.get("acq_time"),
                    "frp": float(row.get("frp", "nan")),
                }
            )
        except (TypeError, ValueError):
            continue
    return {
        "source": source,
        "rows": rows,
        "count": len(rows),
        "window_days": days,
        "note": "Thermal anomaly context only; not a surface-temperature analogue.",
    }


__all__ = ["active_fires"]
