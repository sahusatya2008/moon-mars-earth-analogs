"""PDS3 label parsing and raw image reading for LOLA/MOLA/TES products.

PDS3 labels are deceptively simple key=value records with multi-line values. This parser
handles the fields SNSX SPACE uses and preserves the original label alongside derived products.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def parse_label(path: Path) -> dict[str, str]:
    text = path.read_text(errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    result: dict[str, str] = {}
    # Join continuation lines inside quoted strings.
    lines = text.splitlines()
    joined: list[str] = []
    buf = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("/*") or stripped.startswith("END"):
            if buf:
                joined.append(buf)
                buf = ""
            continue
        if buf:
            buf += " " + stripped
        else:
            buf = stripped
        # A record ends when quotes balance and there is no trailing continuation.
        if buf.count('"') % 2 == 0 and ("=" in buf):
            joined.append(buf)
            buf = ""
    if buf:
        joined.append(buf)
    for line in joined:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().rstrip(";").strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        result[key] = value
    return result


def _dtype(sample_type: str) -> np.dtype:
    st = sample_type.upper()
    if "PC_REAL" in st or "IEEE_REAL" in st:
        return np.dtype("<f4")
    if "MSB" in st or "SUN" in st:
        return np.dtype(">i2")
    if "LSB" in st:
        return np.dtype("<i2")
    if "UNSIGNED" in st:
        return np.dtype(">u2")
    return np.dtype(">i2")


def read_pds_image(img_path: Path, lbl_path: Path) -> tuple[np.ndarray, dict[str, str]]:
    label = parse_label(lbl_path)
    lines = int(label.get("LINES", 0))
    samples = int(label.get("LINE_SAMPLES", 0))
    if not lines or not samples:
        raise ValueError(f"PDS label missing LINES/LINE_SAMPLES: {lbl_path}")
    sample_type = label.get("SAMPLE_TYPE", "MSB_INTEGER")
    dtype = _dtype(sample_type)
    arr = np.fromfile(img_path, dtype=dtype)
    expected = lines * samples
    if arr.size < expected:
        raise ValueError(f"PDS image shorter than label: {arr.size} < {expected}")
    arr = arr[:expected].reshape(lines, samples)
    if np.issubdtype(arr.dtype, np.integer):
        arr = arr.astype(np.float32)
    factor = float(label.get("SCALING_FACTOR", 1.0))
    offset = float(label.get("OFFSET", 0.0))
    arr = arr * factor + offset
    return arr, label


def global_lonlat_transform(label: dict[str, str], width: int, height: int):
    from rasterio.transform import from_origin

    west = float(label.get("WESTERNMOST_LONGITUDE", -180.0))
    east = float(label.get("EASTERNMOST_LONGITUDE", 180.0))
    south = float(label.get("MINIMUM_LATITUDE", -90.0))
    north = float(label.get("MAXIMUM_LATITUDE", 90.0))
    if west > east:
        west -= 360.0
    return from_origin(west, north, (east - west) / width, (north - south) / height)


def write_lonlat_geotiff(array: np.ndarray, out_path: Path, label: dict[str, str], nodata: float = -9999.0) -> Path:
    import rasterio
    from rasterio.transform import from_origin

    out_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = array.shape
    west = float(label.get("WESTERNMOST_LONGITUDE", -180.0))
    east = float(label.get("EASTERNMOST_LONGITUDE", 180.0))
    south = float(label.get("MINIMUM_LATITUDE", -90.0))
    north = float(label.get("MAXIMUM_LATITUDE", 90.0))
    if west > east:
        west -= 360.0
    transform = from_origin(west, north, (east - west) / w, (north - south) / h)
    profile = {
        "driver": "GTiff",
        "width": w,
        "height": h,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": min(512, max(16, w)),
        "blockysize": min(512, max(16, h)),
    }
    data = np.where(np.isfinite(array), array, nodata).astype(np.float32)
    with rasterio.open(out_path, "w", **profile) as ds:
        ds.write(data, 1)
        ds.update_tags(**{k: str(v) for k, v in label.items() if k in {
            "PRODUCT_ID", "DATA_SET_ID", "INSTRUMENT_NAME", "TARGET_NAME", "START_TIME", "STOP_TIME"
        }})
    return out_path
