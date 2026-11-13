"""Configuration path handling for checkout and container layouts."""

from __future__ import annotations

from pathlib import Path

from terrax.config import _resolve_repo_root


def test_repo_root_detection_matches_checkout_layout():
    module = Path("/repo/apps/api/terrax/config.py")
    assert _resolve_repo_root(module) == Path("/repo")


def test_repo_root_detection_matches_container_layout():
    # The image copies apps/api to /app, so the shallow path must not raise IndexError.
    module = Path("/app/terrax/config.py")
    assert _resolve_repo_root(module) == Path("/app")
