"""Fast windowed reads for uncompressed, striped, remote planetary GeoTIFFs.

The USGS LOLA/LOLA and MOLA global mosaics are stored as strip-per-row, uncompressed
GeoTIFFs on S3. GDAL issues one range request per strip, which makes a 40 km window take
minutes. Because the strips are full-width and contiguous, a single HTTP range request can
fetch the whole window in a few seconds. This module parses the classic TIFF directory and
performs that read. Anything unusual (compression, tiling, multiple bands) falls back to
rasterio, so this is an optimisation, not a correctness shortcut.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np
from rasterio.transform import Affine

from .http import SourceUnavailable, client

HEADER_BYTES = 256 * 1024

_IFD_MAX = 4096
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8, 17: 8, 18: 8}


@dataclass
class TiffLayout:
    byte_order: str
    first_ifd: int
    width: int
    height: int
    bits: int
    compression: int
    samples_per_pixel: int
    rows_per_strip: int
    sample_format: int
    strip_offsets: list[int]
    strip_byte_counts: list[int]
    predictor: int
    tiled: bool
    bigtiff: bool = False


def _unpack(order: str, fmt: str, data: bytes, offset: int) -> tuple[int, ...]:
    return struct.unpack_from(order + fmt, data, offset)


def _read_ints(order: str, typ: int, count: int, raw: bytes) -> list[int] | None:
    if typ not in (1, 3, 4, 6, 8, 9, 16):
        return None
    width = _TYPE_SIZES[typ]
    if width * count > len(raw):
        return None
    if typ == 1:
        return list(raw[:count])
    code = {1: "B", 2: "H", 4: "I", 8: "Q"}[width]
    return list(struct.unpack_from(order + code * count, raw, 0))


def _parse_ifd(
    header: bytes,
    base: int = 0,
    *,
    order: str | None = None,
    bigtiff: bool | None = None,
    first_ifd: int | None = None,
) -> tuple[TiffLayout | None, dict[int, tuple[int, int, int, int]], str, int, bool]:
    if len(header) < 16:
        raise SourceUnavailable("remote TIFF header is empty")
    if order is None:
        if header[:2] == b"II":
            order = "<"
        elif header[:2] == b"MM":
            order = ">"
        else:
            raise SourceUnavailable("not a TIFF (bad byte order)")
        magic = struct.unpack_from(order + "H", header, 2)[0]
        if magic == 42:
            bigtiff = False
            first_ifd = struct.unpack_from(order + "I", header, 4)[0]
        elif magic == 43:
            bigtiff = True
            first_ifd = struct.unpack_from(order + "Q", header, 8)[0]
        else:
            raise SourceUnavailable(f"not a TIFF (magic {magic})")
    assert order is not None and bigtiff is not None and first_ifd is not None
    count_size, entry_size, inline = (8, 20, 8) if bigtiff else (2, 12, 4)
    local_ifd = first_ifd - base
    if local_ifd < 0 or local_ifd + count_size > len(header):
        raise SourceUnavailable("TIFF IFD outside header window")
    n_entries = struct.unpack_from(order + ("Q" if bigtiff else "H"), header, local_ifd)[0]
    if n_entries > _IFD_MAX:
        raise SourceUnavailable("implausible TIFF IFD entry count")
    entries: dict[int, tuple[int, int, int, int]] = {}
    for i in range(n_entries):
        pos = local_ifd + count_size + i * entry_size
        if pos + entry_size > len(header):
            break
        tag, typ = _unpack(order, "HH", header, pos)
        if bigtiff:
            count = struct.unpack_from(order + "Q", header, pos + 4)[0]
            value_pos = pos + 12
        else:
            count = struct.unpack_from(order + "I", header, pos + 4)[0]
            value_pos = pos + 8
        entries[tag] = (typ, count, value_pos, inline)

    def ints(tag: int) -> list[int]:
        if tag not in entries:
            return []
        typ, count, pos, inline_size = entries[tag]
        width = _TYPE_SIZES.get(typ, 0)
        if width == 0:
            return []
        total = width * count
        if total <= inline_size:
            return _read_ints(order, typ, count, header[pos : pos + total]) or []
        if bigtiff:
            offset = struct.unpack_from(order + "Q", header, pos)[0]
        else:
            offset = struct.unpack_from(order + "I", header, pos)[0]
        local = offset - base
        if local < 0 or local + total > len(header):
            return []
        return _read_ints(order, typ, count, header[local : local + total]) or []

    width = (ints(256) or [0])[0]
    height = (ints(257) or [0])[0]
    bits_arr = ints(258) or [16]
    compression = (ints(259) or [1])[0]
    samples = (ints(277) or [1])[0]
    rows_per_strip = (ints(278) or [height])[0] or height
    sample_format = (ints(339) or [1])[0]
    predictor = (ints(317) or [1])[0]
    offsets = ints(273)
    counts = ints(279)
    tiled = 322 in entries
    layout = TiffLayout(
        byte_order=order,
        first_ifd=first_ifd,
        width=width,
        height=height,
        bits=bits_arr[0] if len(set(bits_arr)) == 1 else -1,
        compression=compression,
        samples_per_pixel=samples,
        rows_per_strip=rows_per_strip,
        sample_format=sample_format,
        strip_offsets=offsets,
        strip_byte_counts=counts,
        predictor=predictor,
        tiled=tiled,
        bigtiff=bigtiff,
    )
    return layout, entries, order, first_ifd, bigtiff


def _fetch(url: str, start: int, end: int) -> bytes:
    r = client().get(url, headers={"Range": f"bytes={start}-{end}"})
    if r.status_code not in (200, 206):
        raise SourceUnavailable(f"range GET {url} [{start}-{end}] returned HTTP {r.status_code}")
    return r.content


def _prepare(url: str) -> tuple[TiffLayout, bytes, str]:
    # The IFD can be at the end of the file (Mars MOLA); fetch it explicitly if needed.
    prefix = _fetch(url, 0, 31)
    if prefix[:2] == b"II":
        order = "<"
    elif prefix[:2] == b"MM":
        order = ">"
    else:
        raise SourceUnavailable("not a TIFF (bad byte order)")
    magic = struct.unpack_from(order + "H", prefix, 2)[0]
    if magic == 42:
        bigtiff = False
        first_ifd = struct.unpack_from(order + "I", prefix, 4)[0]
    elif magic == 43:
        bigtiff = True
        first_ifd = struct.unpack_from(order + "Q", prefix, 8)[0]
    else:
        raise SourceUnavailable(f"not a TIFF (magic {magic})")
    base = 0 if first_ifd < HEADER_BYTES else first_ifd
    header = prefix if base == 0 else _fetch(url, base, base + HEADER_BYTES - 1)
    layout, entries, order, _first_ifd, bigtiff = _parse_ifd(
        header, base=base, order=order, bigtiff=bigtiff, first_ifd=first_ifd
    )
    assert layout is not None
    # If offsets/counts were truncated, read the arrays explicitly.
    if layout.strip_offsets and layout.strip_byte_counts:
        return layout, header, order
    if 273 not in entries or 279 not in entries:
        raise SourceUnavailable("striped TIFF is missing strip offsets/counts")
    offsets: list[int] = []
    counts: list[int] = []
    for tag, target in ((273, offsets), (279, counts)):
        typ, count, pos, _inline = entries[tag]
        width = _TYPE_SIZES.get(typ, 4)
        if bigtiff:
            offset = struct.unpack_from(order + "Q", header, pos)[0]
        else:
            offset = struct.unpack_from(order + "I", header, pos)[0]
        raw = _fetch(url, offset, offset + width * count - 1)
        target.extend(_read_ints(order, typ, count, raw) or [])
    layout.strip_offsets = offsets
    layout.strip_byte_counts = counts
    return layout, header, order


def read_striped_bbox(
    url: str,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    full_bounds: tuple[float, float, float, float],
    radius_km: float,
    dtype: str = "int16",
    nodata: float = -32768.0,
) -> tuple[np.ndarray, Affine] | None:
    """Read a lon/lat bbox from an uncompressed, strip-per-row TIFF in one range request.

    ``full_bounds`` is the raster extent in body-fixed equirectangular metres. Returns
    ``None`` when the layout is not eligible, so callers can fall back to rasterio.
    """
    layout, header, _order = _prepare(url)
    if (
        layout.tiled
        or layout.compression != 1
        or layout.samples_per_pixel != 1
        or layout.bits not in (8, 16, 32)
        or layout.predictor != 1
        or not layout.strip_offsets
        or len(layout.strip_offsets) != len(layout.strip_byte_counts)
    ):
        return None
    if layout.rows_per_strip != 1:
        # Still eligible if all strips span full rows, but the math below assumes one row.
        return None

    left, bottom, right, top = full_bounds
    px = (right - left) / layout.width
    py = (top - bottom) / layout.height
    col0 = max(0, int(math.floor((west - left) / px)))
    col1 = min(layout.width, int(math.ceil((east - left) / px)))
    row0 = max(0, int(math.floor((top - north) / py)))
    row1 = min(layout.height, int(math.ceil((top - south) / py)))
    if col1 <= col0 or row1 <= row0:
        return None

    stride = layout.width * layout.bits // 8
    first = min(layout.strip_offsets[row0:row1])
    last_offset = max(layout.strip_offsets[row0:row1])
    last_idx = layout.strip_offsets.index(last_offset)
    last_count = layout.strip_byte_counts[last_idx]
    last = last_offset + last_count - 1
    if last - first > 250 * 1024 * 1024:
        return None
    payload = _fetch(url, first, last)

    np_dtype = np.dtype(dtype).newbyteorder("<" if snapshot_order_is_little(layout) else ">")
    array = np.empty((row1 - row0, col1 - col0), dtype=np.float32)
    for out_row, row in enumerate(range(row0, row1)):
        offset = layout.strip_offsets[row] - first
        strip_len = layout.strip_byte_counts[row]
        if offset < 0 or offset + strip_len > len(payload) or strip_len < stride:
            raise SourceUnavailable(f"strip {row} outside fetched range")
        strip = np.frombuffer(payload, dtype=np_dtype, count=layout.width, offset=offset)
        vals = strip[col0:col1].astype(np.float32)
        array[out_row] = vals
    if nodata is not None and np.isfinite(nodata):
        array[array == np.float32(nodata)] = np.nan
    transform = Affine(px, 0.0, left + col0 * px, 0.0, -py, top - row0 * py)
    return array, transform


def snapshot_order_is_little(layout: TiffLayout) -> bool:
    return layout.byte_order == "<"


def read_striped_segment(url: str, west: float, south: float, east: float, north: float, *, radius_km: float) -> tuple[np.ndarray, Affine] | None:
    """Read a lon/lat bbox from a global body-fixed equirectangular TIFF."""
    r_m = radius_km * 1000.0
    return read_striped_bbox(
        url,
        west=math.radians(west) * r_m,
        south=math.radians(south) * r_m,
        east=math.radians(east) * r_m,
        north=math.radians(north) * r_m,
        full_bounds=(
            -math.pi * r_m,
            -math.pi * r_m / 2.0,
            math.pi * r_m,
            math.pi * r_m / 2.0,
        ),
        radius_km=radius_km,
    )


__all__ = ["read_striped_bbox", "read_striped_segment", "TiffLayout"]
