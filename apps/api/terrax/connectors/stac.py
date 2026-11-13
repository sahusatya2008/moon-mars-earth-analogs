"""Earth Search STAC (Sentinel-2 L2A) queries for candidate context."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .http import SourceUnavailable, client

SEARCH = "https://earth-search.aws.element84.com/v1/search"


def search_sentinel2(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    limit: int = 5,
    max_cloud: float = 35.0,
) -> dict[str, Any]:
    today = dt.date.today()
    start = start or (today - dt.timedelta(days=120))
    end = end or today
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": [west, south, east, north],
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "limit": min(20, limit),
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
    }
    try:
        r = client().post(SEARCH, json=payload, timeout=45.0)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise SourceUnavailable(f"Earth Search STAC query failed: {exc}") from exc
    items = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        assets = feature.get("assets", {})
        items.append(
            {
                "id": feature.get("id"),
                "datetime": props.get("datetime"),
                "cloud_cover": props.get("eo:cloud_cover"),
                "platform": props.get("platform"),
                "collection": feature.get("collection"),
                "bbox": feature.get("bbox"),
                "thumbnail": (assets.get("thumbnail") or {}).get("href"),
                "visual": (assets.get("visual") or {}).get("href"),
                "stac_item": feature.get("id"),
            }
        )
    return {
        "source": "Earth Search v1 (AWS Open Data)",
        "collection": "sentinel-2-l2a",
        "items": items,
        "total": len(items),
        "query": payload,
    }


__all__ = ["search_sentinel2"]
