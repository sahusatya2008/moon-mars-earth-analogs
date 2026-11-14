"""Microsoft Planetary Computer STAC search and SAS-signed window reads.

Planetary Computer exposes public STAC metadata, but its raster assets are private Azure
blobs. A short-lived, public SAS token per collection is issued by the Planetary Computer
SAS API; appending it to an asset URL enables anonymous range reads.

Tokens are kept in process memory only and are never written to evidence, manifests, logs,
or error messages. Signed URLs are never persisted: callers receive watershed statistics,
class fractions, and unsigned public preview links.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import threading
import time
import urllib.parse
from typing import Any

import numpy as np
from rasterio.enums import Resampling

from .http import SourceUnavailable, client
from .raster import (
    EARTH_RADIUS_KM,
    GriddedWindow,
    Segment,
    footprint_bbox,
    read_dataset_bbox,
    warp_segments_to_local,
)

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
COLLECTIONS_URL = f"{STAC}/collections"

# STAC metadata changes slowly; search results are cached briefly to avoid repeating the same
# query for every candidate in one analysis.
SEARCH_TTL_S = 900.0
COLLECTION_TTL_S = 3600.0
TOKEN_MARGIN_S = 300.0

DEM_ASSETS = {"nasadem": "elevation", "alos-dem": "data"}

IO_LULC_LEGEND = {
    1: "Water",
    2: "Trees",
    4: "Flooded vegetation",
    5: "Crops",
    7: "Built area",
    8: "Bare ground",
    9: "Snow/ice",
    10: "Clouds",
    11: "Rangeland",
}

# Broad classes used only to compare two land-cover products with different taxonomies.
WORLDCOVER_BUCKETS = {
    10: "trees",
    20: "rangeland",
    30: "rangeland",
    40: "crops",
    50: "built",
    60: "bare",
    70: "snow",
    80: "water",
    90: "wetland",
    95: "wetland",
    100: "bare",
}
IO_LULC_BUCKETS = {
    1: "water",
    2: "trees",
    4: "wetland",
    5: "crops",
    7: "built",
    8: "bare",
    9: "snow",
    10: "cloud",
    11: "rangeland",
}

_token_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}
_search_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_collection_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_BLOB_URL = re.compile(r"(https://[^\s?]+\.blob\.core\.windows\.net/[^\s?]+)\?[^\s)\]]+")
_SAS_VALUE = re.compile(r"([?&](?:st|se|sp|sv|sr|skoid|sktid|skt|ske|sks|skv|sig)=)[^&\s)\]]+")


def _is_blob(href: str) -> bool:
    try:
        host = urllib.parse.urlparse(href).hostname or ""
    except ValueError:
        return False
    return host.endswith(".blob.core.windows.net")


def _redact(text: str, token: str | None = None) -> str:
    """Remove SAS tokens and signed-query strings from an error before it is persisted."""
    value = str(text)
    if token:
        value = value.replace(token, "<sas>")
    value = _BLOB_URL.sub(r"\1?<sas>", value)
    value = _SAS_VALUE.sub(r"\1<sas>", value)
    return value[:600]


def _token_expiry(token: str) -> float:
    query = urllib.parse.parse_qs(token)
    raw = (query.get("se") or [None])[0]
    if raw:
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time() + 1800.0


def _fetch_token(collection: str) -> str:
    url = TOKEN_URL.format(collection=urllib.parse.quote(collection, safe=""))
    try:
        response = client().get(url, timeout=45.0)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise SourceUnavailable(
            f"Planetary Computer SAS token unavailable for {collection}: {_redact(exc)}"
        ) from None
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise SourceUnavailable(f"Planetary Computer SAS token response for {collection} was empty")
    return token


def token(collection: str) -> str:
    """Return a cached public SAS token for a collection, refreshing before expiry."""
    now = time.time()
    with _token_lock:
        cached = _tokens.get(collection)
        if cached and cached[1] > now + TOKEN_MARGIN_S:
            return cached[0]
        value = _fetch_token(collection)
        _tokens[collection] = (value, _token_expiry(value))
        return value


def _invalidate_token(collection: str) -> None:
    with _token_lock:
        _tokens.pop(collection, None)


def sign_href(href: str, collection: str) -> str:
    """Append the collection SAS token to an Azure blob URL.

    Non-blob URLs are returned unchanged so a token is never sent to an unrelated host.
    """
    if not href or not _is_blob(href):
        return href
    separator = "&" if "?" in href else "?"
    return f"{href}{separator}{token(collection)}"


def collection_metadata(collection: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _collection_cache.get(collection)
    if cached and cached[0] > now:
        return cached[1]
    url = f"{COLLECTIONS_URL}/{urllib.parse.quote(collection, safe='')}"
    try:
        response = client().get(url, timeout=30.0)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise SourceUnavailable(
            f"Planetary Computer collection metadata failed for {collection}: {_redact(exc)}"
        ) from None
    _collection_cache[collection] = (now + COLLECTION_TTL_S, payload)
    return payload


def collection_url(collection: str) -> str:
    return f"{COLLECTIONS_URL}/{urllib.parse.quote(collection, safe='')}"


def item_url(collection: str, item_id: str) -> str:
    return f"{collection_url(collection)}/items/{urllib.parse.quote(item_id, safe='')}"


def _search_bboxes(bbox: tuple[float, float, float, float]) -> list[list[float]]:
    west, south, east, north = bbox
    if west <= east:
        return [[west, south, east, north]]
    # Antimeridian-crossing footprint: STAC bboxes may not wrap, so query both halves.
    return [[west, south, 180.0, north], [-180.0, south, east, north]]


def search(
    collection: str,
    bbox: tuple[float, float, float, float] | list[float] | None = None,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    limit: int = 10,
    query: dict[str, Any] | None = None,
    sort_desc: bool = True,
    ttl: float = SEARCH_TTL_S,
) -> dict[str, Any]:
    """Run a cached Planetary Computer STAC item search."""
    payload: dict[str, Any] = {"collections": [collection], "limit": max(1, min(100, int(limit)))}
    if bbox is not None:
        payload["bbox"] = [float(v) for v in bbox]
    if start or end:
        start_text = (start or dt.date(1970, 1, 1)).isoformat()
        end_text = (end or dt.date.today()).isoformat()
        payload["datetime"] = f"{start_text}T00:00:00Z/{end_text}T23:59:59Z"
    if query:
        payload["query"] = query
    if sort_desc:
        payload["sortby"] = [{"field": "properties.datetime", "direction": "desc"}]

    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    now = time.monotonic()
    cached = _search_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    try:
        response = client().post(f"{STAC}/search", json=payload, timeout=60.0)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise SourceUnavailable(
            f"Planetary Computer STAC search failed for {collection}: {_redact(exc)}"
        ) from None
    if not isinstance(data, dict) or "features" not in data:
        raise SourceUnavailable(
            f"Planetary Computer STAC search for {collection} returned no feature collection"
        )
    _search_cache[key] = (now + ttl, data)
    return data


def _item_datetime(feature: dict[str, Any]) -> str | None:
    properties = feature.get("properties") or {}
    value = (
        properties.get("datetime")
        or properties.get("start_datetime")
        or properties.get("end_datetime")
    )
    return str(value) if value else None


def search_footprint(
    collection: str,
    footprint: tuple[float, float, float, float],
    **kwargs: Any,
) -> dict[str, Any]:
    """Search a lon/lat footprint, splitting at the antimeridian and de-duplicating items."""
    features: dict[str, dict[str, Any]] = {}
    payloads: list[dict[str, Any]] = []
    for part in _search_bboxes(footprint):
        data = search(collection, part, **kwargs)
        payloads.append(data.get("query") or {"bbox": part})
        for feature in data.get("features", []):
            item_id = feature.get("id")
            if item_id:
                features[item_id] = feature
    ordered = list(features.values())
    ordered.sort(
        key=lambda feature: str(_item_datetime(feature) or ""),
        reverse=True,
    )
    return {
        "type": "FeatureCollection",
        "features": ordered,
        "query": {"collection": collection, "bboxes": payloads},
    }


def _asset_href(feature: dict[str, Any], asset: str | None = None) -> str:
    assets = feature.get("assets") or {}
    if asset and (assets.get(asset) or {}).get("href"):
        return str(assets[asset]["href"])
    for name in ("data", "elevation", "map", "visual"):
        href = (assets.get(name) or {}).get("href")
        if href:
            return str(href)
    raise SourceUnavailable(f"Planetary Computer item {feature.get('id')} has no raster asset")


def _intersects(item_bbox: Any, bbox: tuple[float, float, float, float]) -> bool:
    if not item_bbox or not isinstance(item_bbox, (list, tuple)) or len(item_bbox) < 4:
        return True
    iw, isouth, ie, inorth = (float(v) for v in item_bbox[:4])
    for west, south, east, north in _search_bboxes(bbox):
        if not (east <= iw or west >= ie or north <= isouth or south >= inorth):
            return True
    return False


def read_signed_segments(
    collection: str,
    href: str,
    bbox: tuple[float, float, float, float],
    *,
    decimated_max: int = 2048,
    categorical: bool = False,
    radius_km: float = EARTH_RADIUS_KM,
) -> tuple[list[Segment], dict[str, Any]]:
    """Read a window from a SAS-gated Planetary Computer asset.

    Retries once with a fresh token if Azure reports an authentication failure. Product
    metadata is returned with the unsigned href so no SAS token can reach persistence.
    """
    last_error: Exception | None = None
    for attempt in (1, 2):
        signed = sign_href(href, collection)
        try:
            segments, meta = read_dataset_bbox(
                signed,
                bbox,
                decimated_max=decimated_max,
                radius_km=radius_km,
                resampling=Resampling.nearest if categorical else Resampling.bilinear,
            )
            meta = dict(meta)
            meta["source"] = href
            meta["signed"] = signed != href
            return segments, meta
        except Exception as exc:
            last_error = exc
            message = str(exc)
            auth_failure = any(
                marker in message
                for marker in ("403", "401", "AuthorizationFailure", "AuthenticationFailed")
            )
            if attempt == 1 and auth_failure:
                _invalidate_token(collection)
                continue
            break
    raise SourceUnavailable(
        f"Planetary Computer window read failed for {collection}: {_redact(last_error)}"
    ) from None


def _grid_stats(
    primary: GriddedWindow,
    secondary: GriddedWindow,
    *,
    reference_dataset_id: str,
) -> dict[str, Any]:
    if primary.width != secondary.width or primary.height != secondary.height:
        raise ValueError(
            f"cross-check grids differ: {primary.width}x{primary.height} vs "
            f"{secondary.width}x{secondary.height}"
        )
    primary_values = np.asarray(primary.values, dtype=np.float64)
    secondary_values = np.asarray(secondary.values, dtype=np.float64)
    primary_valid = np.isfinite(primary_values) & np.asarray(primary.valid, dtype=bool)
    secondary_valid = np.isfinite(secondary_values) & np.asarray(secondary.valid, dtype=bool)
    overlap = primary_valid & secondary_valid
    count = int(overlap.sum())
    result: dict[str, Any] = {
        "reference_dataset_id": reference_dataset_id,
        "primary_valid_pixels": int(primary_valid.sum()),
        "secondary_valid_pixels": int(secondary_valid.sum()),
        "overlap_pixels": count,
        "overlap_fraction_of_primary": round(
            count / max(1, int(primary_valid.sum())), 6
        ),
        "secondary_coverage_fraction": round(float(secondary_valid.mean()), 6),
        "primary_elevation_range_m": [
            round(float(primary_values[primary_valid].min()), 3),
            round(float(primary_values[primary_valid].max()), 3),
        ]
        if primary_valid.any()
        else None,
        "secondary_elevation_range_m": [
            round(float(secondary_values[secondary_valid].min()), 3),
            round(float(secondary_values[secondary_valid].max()), 3),
        ]
        if secondary_valid.any()
        else None,
        "comparable": count >= 16,
        "caveat": (
            "Independent DEM cross-check only; this product is not used in scoring. "
            "Vertical datums, acquisition epochs, and surface definitions differ, so bias "
            "and RMSE are not a product accuracy assessment."
        ),
    }
    if count < 2:
        result.update(
            {
                "bias_m": None,
                "mae_m": None,
                "rmse_m": None,
                "median_bias_m": None,
                "p95_abs_deviation_m": None,
                "pearson_r": None,
            }
        )
        return result
    difference = secondary_values[overlap] - primary_values[overlap]
    correlation = float(np.corrcoef(primary_values[overlap], secondary_values[overlap])[0, 1])
    result.update(
        {
            "bias_m": round(float(np.mean(difference)), 4),
            "mae_m": round(float(np.mean(np.abs(difference))), 4),
            "rmse_m": round(float(np.sqrt(np.mean(difference * difference))), 4),
            "median_bias_m": round(float(np.median(difference)), 4),
            "p95_abs_deviation_m": round(float(np.percentile(np.abs(difference), 95)), 4),
            "pearson_r": round(correlation, 6) if math.isfinite(correlation) else None,
        }
    )
    return result


def dem_crosscheck(
    collection: str,
    lon: float,
    lat: float,
    size_km: float,
    reference: GriddedWindow,
    *,
    reference_dataset_id: str,
    dataset_id: str | None = None,
    asset: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Compare a Planetary Computer DEM with a candidate's refined DEM window."""
    bbox = tuple(footprint_bbox(lon, lat, size_km, EARTH_RADIUS_KM)[:4])
    data = search_footprint(collection, bbox, limit=limit)  # type: ignore[arg-type]
    features = data.get("features", [])
    if not features:
        raise SourceUnavailable(f"Planetary Computer collection {collection} has no tile here")

    asset_name = asset or DEM_ASSETS.get(collection)
    segments: list[Segment] = []
    item_ids: list[str] = []
    failed: list[str] = []
    native_resolution: float | None = None
    for feature in features:
        feature_bbox = feature.get("bbox")
        if feature_bbox and not _intersects(feature_bbox, bbox):  # type: ignore[arg-type]
            continue
        try:
            href = _asset_href(feature, asset_name)
            tile, meta = read_signed_segments(
                collection,
                href,
                bbox,  # type: ignore[arg-type]
                decimated_max=4096,
            )
        except Exception:
            failed.append(str(feature.get("id")))
            continue
        segments.extend(tile)
        item_ids.append(str(feature.get("id")))
        if native_resolution is None:
            native_resolution = meta.get("native_resolution_m")  # type: ignore[assignment]

    if not segments:
        raise SourceUnavailable(
            f"Planetary Computer collection {collection} returned no readable elevation window"
        )

    secondary = warp_segments_to_local(
        segments,
        lon,
        lat,
        size_km,
        EARTH_RADIUS_KM,
        reference.resolution_m,
    )
    result = _grid_stats(
        reference,
        secondary,
        reference_dataset_id=reference_dataset_id,
    )
    result.update(
        {
            "collection": collection,
            "dataset_id": dataset_id or collection,
            "asset": asset_name,
            "item_ids": item_ids,
            "failed_item_ids": failed,
            "native_resolution_m": native_resolution,
            "analysis_resolution_m": reference.resolution_m,
            "source_url": collection_url(collection),
        }
    )
    return result


def lulc_fractions(
    collection: str,
    lon: float,
    lat: float,
    size_km: float,
    *,
    prefer_year: int | None = None,
    max_pixels: int = 1024,
) -> dict[str, Any]:
    """Class fractions from Planetary Computer's annual 10 m IO LULC product."""
    bbox = tuple(footprint_bbox(lon, lat, size_km, EARTH_RADIUS_KM)[:4])
    data = search_footprint(collection, bbox, limit=100)  # type: ignore[arg-type]
    features = [
        feature for feature in data.get("features", []) if _item_datetime(feature)
    ]
    if not features:
        raise SourceUnavailable(f"Planetary Computer collection {collection} has no tile here")
    years = sorted(
        {
            int(str(_item_datetime(feature))[:4])
            for feature in features
            if str(_item_datetime(feature))[:4].isdigit()
        }
    )
    if not years:
        raise SourceUnavailable(f"Planetary Computer collection {collection} items have no year")
    year = prefer_year if prefer_year in years else years[-1]
    selected = [
        feature
        for feature in features
        if int(str(_item_datetime(feature))[:4]) == year
    ]

    values: list[np.ndarray] = []
    item_ids: list[str] = []
    failed: list[str] = []
    for feature in selected:
        try:
            href = _asset_href(feature, "data")
            segments, _meta = read_signed_segments(
                collection,
                href,
                bbox,  # type: ignore[arg-type]
                decimated_max=max_pixels,
                categorical=True,
            )
        except Exception:
            failed.append(str(feature.get("id")))
            continue
        item_ids.append(str(feature.get("id")))
        for segment in segments:
            array = np.asarray(segment.array, dtype=np.float32)
            valid = np.isfinite(array) & (array >= 1) & (array <= 11)
            if valid.any():
                values.append(np.rint(array[valid]).astype(np.int16))

    if not values:
        raise SourceUnavailable(
            f"Planetary Computer collection {collection} returned no readable class pixels"
        )
    all_values = np.concatenate(values)
    classes, counts = np.unique(all_values, return_counts=True)
    total = int(counts.sum())
    fractions = {
        IO_LULC_LEGEND.get(int(code), f"Class {int(code)}"): round(float(count) / total, 5)
        for code, count in sorted(zip(classes, counts, strict=False), key=lambda pair: -pair[1])
    }
    by_code = {
        str(int(code)): round(float(count) / total, 5)
        for code, count in zip(classes, counts, strict=False)
    }
    dominant = max(fractions.items(), key=lambda item: item[1])[0]
    return {
        "source": "Impact Observatory / Esri / Microsoft 10 m Annual LULC V2",
        "collection": collection,
        "year": year,
        "available_years": years,
        "resolution_m": 10.0,
        "classes": fractions,
        "class_codes": by_code,
        "dominant": dominant,
        "valid_pixels": total,
        "item_ids": item_ids,
        "failed_item_ids": failed,
        "sample_resolution_m": round(
            (max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 111_320.0)
            / max(1, int(math.sqrt(total))),
            1,
        ),
    }


def _bucket_fractions(
    class_codes: dict[str, float], mapping: dict[int, str]
) -> dict[str, float]:
    buckets: dict[str, float] = {}
    for code, fraction in class_codes.items():
        try:
            bucket = mapping.get(int(float(code)))
        except (TypeError, ValueError):
            bucket = None
        if bucket:
            buckets[bucket] = buckets.get(bucket, 0.0) + float(fraction)
    total = sum(buckets.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in buckets.items()}


def landcover_agreement(worldcover: dict[str, Any], lulc: dict[str, Any]) -> dict[str, Any]:
    """Compare WorldCover and IO LULC on broad, product-independent classes."""
    first = _bucket_fractions(worldcover.get("class_codes") or {}, WORLDCOVER_BUCKETS)
    second = _bucket_fractions(lulc.get("class_codes") or {}, IO_LULC_BUCKETS)
    if not first or not second:
        return {"available": False, "reason": "one product had no comparable classes"}
    keys = sorted(set(first) | set(second))
    total_variation = 0.5 * sum(abs(first.get(key, 0.0) - second.get(key, 0.0)) for key in keys)
    agreement = max(0.0, min(1.0, 1.0 - total_variation))
    return {
        "available": True,
        "agreement": round(agreement, 5),
        "total_variation": round(total_variation, 5),
        "worldcover_dominant": worldcover.get("dominant"),
        "lulc_dominant": lulc.get("dominant"),
        "worldcover_year": 2021,
        "lulc_year": lulc.get("year"),
        "buckets": {
            "worldcover": {key: round(first.get(key, 0.0), 5) for key in keys},
            "lulc": {key: round(second.get(key, 0.0), 5) for key in keys},
        },
        "method": "1 - 0.5 * sum(abs(p - q)) after mapping both legends to broad classes",
        "caveat": (
            "Products use different class definitions and epochs (WorldCover 2021 vs IO LULC "
            f"{lulc.get('year')}). Agreement is contextual evidence, not a validation."
        ),
    }


def optical_context(
    collection: str,
    lon: float,
    lat: float,
    size_km: float,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    limit: int = 3,
    cloud_lt: float | None = 45.0,
) -> dict[str, Any]:
    """Find recent optical scenes and return unsigned metadata and preview links."""
    bbox = tuple(footprint_bbox(lon, lat, size_km, EARTH_RADIUS_KM)[:4])
    query = {"eo:cloud_cover": {"lt": float(cloud_lt)}} if cloud_lt is not None else None
    data = search_footprint(
        collection,
        bbox,  # type: ignore[arg-type]
        start=start,
        end=end,
        limit=limit,
        query=query,
    )
    if not data.get("features") and cloud_lt is not None:
        data = search_footprint(
            collection,
            bbox,  # type: ignore[arg-type]
            start=start,
            end=end,
            limit=limit,
            query=None,
        )
    items: list[dict[str, Any]] = []
    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        assets = feature.get("assets") or {}
        preview = (
            (assets.get("rendered_preview") or {}).get("href")
            or (assets.get("thumbnail") or {}).get("href")
            or (assets.get("visual") or {}).get("href")
        )
        item_id = str(feature.get("id"))
        items.append(
            {
                "id": item_id,
                "datetime": _item_datetime(feature),
                "cloud_cover": properties.get("eo:cloud_cover"),
                "platform": properties.get("platform"),
                "constellation": properties.get("constellation"),
                "preview_url": preview,
                "stac_item_url": item_url(collection, item_id),
            }
        )
    return {
        "collection": collection,
        "source": collection_url(collection),
        "query": {
            "bbox": list(bbox),
            "datetime": (
                f"{start.isoformat() if start else 'open'}..{end.isoformat() if end else 'open'}"
            ),
            "cloud_cover_lt": cloud_lt,
        },
        "items": items,
    }


def probe(collection: str) -> tuple[str, str, int | None]:
    """Probe public STAC metadata and the automatic SAS token path."""
    url = collection_url(collection)
    try:
        response = client().get(url, timeout=20.0)
    except Exception as exc:
        return "unavailable", _redact(exc), None
    if response.status_code >= 400:
        status = "requires_credentials" if response.status_code in (401, 403) else "degraded"
        return status, f"HTTP {response.status_code} for STAC metadata", response.status_code
    try:
        value = token(collection)
    except SourceUnavailable as exc:
        return "degraded", str(exc), response.status_code
    expiry = dt.datetime.fromtimestamp(_token_expiry(value), tz=dt.UTC)
    return (
        "available",
        f"STAC metadata HTTP {response.status_code}; public SAS token issued "
        f"(expires {expiry.date().isoformat()})",
        response.status_code,
    )


__all__ = [
    "STAC",
    "collection_metadata",
    "collection_url",
    "dem_crosscheck",
    "item_url",
    "landcover_agreement",
    "lulc_fractions",
    "optical_context",
    "probe",
    "read_signed_segments",
    "search",
    "search_footprint",
    "sign_href",
    "token",
]
