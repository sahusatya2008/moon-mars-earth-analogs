"""NASA CMR collection discovery for traceable mission context."""

from __future__ import annotations

import time
from typing import Any

from .http import SourceUnavailable, get_json

CMR = "https://cmr.earthdata.nasa.gov/search"


def search_collections(keyword: str, *, page_size: int = 8, provider: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "keyword": keyword,
        "page_size": min(20, page_size),
        "sort_key": "-score",
    }
    if provider:
        params["provider"] = provider
    started = time.time()
    try:
        data = get_json(f"{CMR}/collections.json", params=params)
    except Exception as exc:
        raise SourceUnavailable(f"CMR collection search failed: {exc}") from exc
    entries = data.get("feed", {}).get("entry", []) or []
    items = []
    for e in entries:
        items.append(
            {
                "id": e.get("id"),
                "concept_id": e.get("concept-id"),
                "title": e.get("title"),
                "summary": (e.get("summary") or "")[:800],
                "short_name": e.get("short_name"),
                "version_id": e.get("version_id"),
                "provider": e.get("data_center"),
                "doi": e.get("doi"),
                "links": [
                    link.get("href")
                    for link in (e.get("links") or [])
                    if link.get("href")
                    and link.get("type") in (None, "application/xml", "text/html")
                ][:4],
            }
        )
    return {
        "source": "NASA CMR",
        "items": items,
        "total": int(data.get("feed", {}).get("hits", len(items))),
        "query": params,
        "took_ms": round((time.time() - started) * 1000, 1),
    }


def body_keywords(body_id: str) -> list[str]:
    return {
        "moon": ["LOLA", "LROC", "Moon mineralogy"],
        "mars": ["MOLA", "HiRISE", "Mars surface composition"],
        "earth": ["Copernicus DEM", "Sentinel-2", "Landsat"],
    }.get(body_id, [])


__all__ = ["search_collections", "body_keywords"]
