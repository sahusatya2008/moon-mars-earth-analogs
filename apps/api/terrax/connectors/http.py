"""Shared HTTP and caching utilities for public scientific sources."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings

settings = get_settings()

DEFAULT_HEADERS = {
    "User-Agent": settings.http_user_agent,
    "Accept-Encoding": "gzip, deflate",
}


class SourceUnavailable(RuntimeError):
    """Raised when a real external source cannot be reached or parsed."""


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers=DEFAULT_HEADERS,
            timeout=httpx.Timeout(settings.http_timeout_seconds, connect=20.0),
            follow_redirects=True,
        )
    return _client


@retry(
    retry=retry_if_exception_type((httpx.HTTPError, SourceUnavailable)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    try:
        r = client().get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceUnavailable(f"GET {url} failed: {exc}") from exc


@retry(
    retry=retry_if_exception_type((httpx.HTTPError, SourceUnavailable)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def get_bytes(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> bytes:
    try:
        r = client().get(url, params=params, headers=headers, timeout=timeout or settings.http_timeout_seconds)
        r.raise_for_status()
        return r.content
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"GET {url} failed: {exc}") from exc


def head(url: str, timeout: float = 30.0) -> httpx.Response:
    r = client().head(url, timeout=timeout)
    return r


def safe_name(value: str, max_len: int = 120) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned[:max_len] or "source"


def cache_path(*parts: str) -> Path:
    path = settings.cache_dir.joinpath(*[safe_name(p) for p in parts])
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def download(
    url: str,
    target: Path,
    *,
    min_bytes: int = 1024,
    resume: bool = True,
    progress: Any | None = None,
) -> Path:
    """Download a source product with resume and atomic rename.

    Uses a Range request when a partial file exists. Never writes outside the cache path
    supplied by the caller.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size >= min_bytes:
        return target

    partial = target.with_suffix(target.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() and resume else 0
    headers = dict(DEFAULT_HEADERS)
    if existing:
        headers["Range"] = f"bytes={existing}-"
    mode = "ab" if existing else "wb"
    with client().stream("GET", url, headers=headers) as response:
        if response.status_code == 416:
            partial.rename(target)
            return target
        response.raise_for_status()
        total = int(response.headers.get("content-length", "0")) + existing
        written = existing
        with partial.open(mode) as fh:
            for chunk in response.iter_bytes(1024 * 256):
                fh.write(chunk)
                written += len(chunk)
                if progress and total:
                    progress(min(written / total, 1.0), written, total)
    if partial.stat().st_size < min_bytes:
        raise SourceUnavailable(f"Downloaded file too small: {partial} ({partial.stat().st_size} bytes)")
    partial.replace(target)
    return target


def cached_json(key: str, payload: dict[str, Any]) -> Path:
    path = cache_path("json", key + ".json")
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def gdal_env() -> dict[str, str]:
    return {
        # Planetary rasters use non-Earth radii; PROJ must allow cross-body reprojection
        # between body-fixed equirectangular and body-fixed local azimuthal grids.
        "PROJ_IGNORE_CELESTIAL_BODY": "YES",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIPLEX": "YES",
        "GDAL_HTTP_VERSION": "2",
        "GDAL_HTTP_MAX_RETRY": "3",
        "GDAL_HTTP_RETRY_DELAY": "2",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff,.TIFF,.img,.IMG,.jp2,.JP2",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "67108864",
        "GDAL_CACHEMAX": "512",
        "CPL_TMPDIR": str(settings.cache_dir / "tmp"),
    }


def apply_gdal_env() -> None:
    (settings.cache_dir / "tmp").mkdir(parents=True, exist_ok=True)
    for key, value in gdal_env().items():
        os.environ.setdefault(key, value)


def timed(fn: Any, *args: Any, **kwargs: Any) -> tuple[Any, float]:
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, round((time.perf_counter() - start) * 1000, 1)
