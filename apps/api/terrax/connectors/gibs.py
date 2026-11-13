"""NASA GIBS WMS access for real Earth imagery (textures and candidate previews)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from ..config import get_settings
from .http import SourceUnavailable, get_bytes

settings = get_settings()

BASE = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
LAYER = "BlueMarble_ShadedRelief_Bathymetry"
TRUE_COLOR = "VIIRS_SNPP_CorrectedReflectance_TrueColor"


def wms_url(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    width: int = 512,
    height: int = 512,
    layer: str = LAYER,
    time: str | None = None,
) -> str:
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": "1.1.1",
        "LAYERS": layer,
        "STYLES": "",
        "FORMAT": "image/jpeg",
        "TRANSPARENT": "FALSE",
        "SRS": "EPSG:4326",
        "BBOX": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}",
        "WIDTH": str(width),
        "HEIGHT": str(height),
    }
    if time:
        params["TIME"] = time
    return f"{BASE}?{urlencode(params)}"


def preview_url(west: float, south: float, east: float, north: float, *, width: int = 512, height: int = 512) -> str:
    return wms_url(west, south, east, north, width=width, height=height)


def download_preview(
    west: float,
    south: float,
    east: float,
    north: float,
    target: Path,
    *,
    width: int = 512,
    height: int = 512,
) -> Path:
    if target.exists() and target.stat().st_size > 5_000:
        return target
    url = preview_url(west, south, east, north, width=width, height=height)
    try:
        data = get_bytes(url, timeout=60)
    except Exception as exc:
        raise SourceUnavailable(f"GIBS preview failed: {exc}") from exc
    if not data[:2] == b"\xff\xd8":
        raise SourceUnavailable("GIBS did not return a JPEG image")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def texture_path(width: int = 2048) -> Path:
    target = settings.cache_dir / "textures" / f"earth_{width}.jpg"
    if target.exists() and target.stat().st_size > 10_000:
        return target
    height = width // 2
    download_preview(-180.0, -90.0, 180.0, 90.0, target, width=width, height=height)
    return target


def probe() -> tuple[str, str, int | None]:
    try:
        url = wms_url(-10, 30, 10, 50, width=64, height=64)
        data = get_bytes(url, timeout=30)
        if data[:2] == b"\xff\xd8":
            return "available", "GIBS WMS returned a JPEG for a small test bbox", 200
        return "degraded", "GIBS returned a non-JPEG response", None
    except Exception as exc:
        return "unavailable", str(exc)[:300], None


__all__ = ["wms_url", "preview_url", "download_preview", "texture_path", "probe", "LAYER", "TRUE_COLOR"]
