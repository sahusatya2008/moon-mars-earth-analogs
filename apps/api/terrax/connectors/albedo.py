"""Evidence-only surface albedo windows (not a cross-body similarity input)."""

from __future__ import annotations

from ..config import get_settings
from .http import SourceUnavailable
from .raster import MARS_RADIUS_KM, MOON_RADIUS_KM, footprint_bbox, sample_raster_bbox

settings = get_settings()

LOLA_ALBEDO = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/Lunar_LRO_LOLA_Albedo_Global_10ppd.tif"
TES_ALBEDO = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/Mars_MGS_TES_Albedo_mosaic_global_7410m.tif"

CAVEAT = (
    "Normal albedo from different instruments and wavelength bands is not physically "
    "equivalent across bodies. It is displayed as context and never contributes to the score."
)


def body_albedo(body_id: str, lon: float, lat: float, size_km: float = 40.0) -> dict | None:
    try:
        if body_id == "moon":
            bbox = footprint_bbox(lon, lat, size_km, MOON_RADIUS_KM)
            stats = sample_raster_bbox(LOLA_ALBEDO, bbox[:4], radius_km=MOON_RADIUS_KM)
            return {
                "dataset_id": "lro_lola_albedo_10ppd",
                "title": "LOLA normal albedo (10 pixels/degree)",
                "unit": "instrument DN (relative)",
                "statistics": stats,
                "source_url": LOLA_ALBEDO,
                "citation": "LRO LOLA normal albedo global mosaic, PDS Geosciences Node.",
                "caveat": CAVEAT,
            }
        if body_id == "mars":
            bbox = footprint_bbox(lon, lat, size_km, MARS_RADIUS_KM)
            stats = sample_raster_bbox(TES_ALBEDO, bbox[:4], radius_km=MARS_RADIUS_KM)
            return {
                "dataset_id": "mgs_tes_albedo_7410m",
                "title": "MGS TES bolometric albedo (7.4 km)",
                "unit": "albedo (0-1)",
                "statistics": stats,
                "source_url": TES_ALBEDO,
                "citation": "MGS TES global albedo mosaic, PDS Geosciences Node.",
                "caveat": CAVEAT,
            }
        return None
    except SourceUnavailable:
        return None
    except Exception:
        return None


__all__ = ["body_albedo", "CAVEAT", "LOLA_ALBEDO", "TES_ALBEDO"]
