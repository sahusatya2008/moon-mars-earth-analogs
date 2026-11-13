"""ESA WorldCover 10 m land-cover fractions (evidence/context, not a similarity input)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .http import SourceUnavailable
from .raster import EARTH_RADIUS_KM, read_dataset_bbox

BASE = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
LEGEND = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / sparse vegetation",
    70: "Snow and ice",
    80: "Permanent water bodies",
    90: "Herbaceous wetland",
    95: "Mangroves",
    100: "Moss and lichen",
}


def _tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat):02d}{ew}{abs(lon):03d}_Map.tif"


def _tile_url(lat: int, lon: int) -> str:
    return f"{BASE}/{_tile_name(lat, lon)}"


def class_fractions(west: float, south: float, east: float, north: float, *, max_pixels: int = 512) -> dict[str, Any]:
    # WorldCover uses 3° x 3° tiles aligned to multiples of three.
    lat0 = int(math.floor(south / 3.0) * 3)
    lat1 = int(math.floor((north - 1e-9) / 3.0) * 3)
    lon0 = int(math.floor(west / 3.0) * 3)
    lon1 = int(math.floor((east - 1e-9) / 3.0) * 3)
    values = []
    covered_area = 0.0
    for lat in range(lat0, lat1 + 1, 3):
        for lon in range(lon0, lon1 + 1, 3):
            tw, ts, te, tn = lon, lat, lon + 3, lat + 3
            w, s = max(west, tw), max(south, ts)
            e, n = min(east, te), min(north, tn)
            if e <= w or n <= s:
                continue
            try:
                segments, _meta = read_dataset_bbox(
                    _tile_url(lat, lon), (w, s, e, n), decimated_max=max_pixels, radius_km=EARTH_RADIUS_KM
                )
            except Exception:
                continue
            for seg in segments:
                arr = np.asarray(seg.array)
                vals = arr[np.isfinite(arr)]
                vals = vals[(vals > 0) & (vals <= 100)]
                if vals.size:
                    values.append(vals.astype(np.uint8))
                    covered_area += float(vals.size)
    if not values:
        raise SourceUnavailable("WorldCover returned no valid land-cover pixels for this window")
    allv = np.concatenate(values)
    classes, counts = np.unique(allv, return_counts=True)
    total = int(counts.sum())
    fractions = {
        LEGEND.get(int(c), f"Class {int(c)}"): round(float(n) / total, 5)
        for c, n in sorted(zip(classes, counts, strict=False), key=lambda x: -x[1])
    }
    dominant = max(fractions.items(), key=lambda kv: kv[1])[0]
    return {
        "source": "ESA WorldCover 2021 v200",
        "resolution_m": 10.0,
        "classes": fractions,
        "dominant": dominant,
        "valid_pixels": total,
        "sample_resolution_m": round(
            (max(east - west, north - south) * 111_320.0) / max(1, int(math.sqrt(total))), 1
        ),
    }


__all__ = ["class_fractions", "LEGEND", "tile_url" if False else "_tile_url"]
