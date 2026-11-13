"""NOAA ETOPO 2022 60 arc-second topography/bathymetry for global screening.

Used only for coarse candidate discovery. Refined candidates are always re-measured
with Copernicus DEM GLO-30. ETOPO is not used as final high-resolution evidence.
"""

from __future__ import annotations

from pathlib import Path

from ..config import get_settings
from .http import download, head
from .raster import Segment, read_dataset_bbox

settings = get_settings()

URL = (
    "https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/60s/"
    "60s_surface_elev_gtif/ETOPO_2022_v1_60s_N90W180_surface.tif"
)
EXPECTED_BYTES = 465_969_062


def local_path() -> Path:
    return settings.cache_dir / "sources" / "ETOPO_2022_v1_60s_N90W180_surface.tif"


def ensure_local() -> Path:
    path = local_path()
    if path.exists() and path.stat().st_size > 100_000_000:
        return path
    download(URL, path, min_bytes=100_000_000)
    return path


def read_bbox(west: float, south: float, east: float, north: float) -> list[Segment]:
    segments, _ = read_dataset_bbox(str(ensure_local()), (west, south, east, north), decimated_max=2048)
    return segments


def availability() -> tuple[str, str, str | None, int | None]:
    if local_path().exists() and local_path().stat().st_size > 100_000_000:
        return "available", "Local cached ETOPO 2022 60s COG", URL, 200
    try:
        r = head(URL, timeout=30)
        if r.status_code in (200, 206):
            return "available", "Reachable; will download once on first global screen", URL, r.status_code
        return "degraded", f"HTTP {r.status_code}", URL, r.status_code
    except Exception as exc:
        return "unavailable", str(exc)[:300], URL, None
