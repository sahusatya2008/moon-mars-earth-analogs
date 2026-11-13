"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AuditEvent

settings = get_settings()


def pagination(
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(25, ge=1, le=200),
) -> tuple[int, int]:
    return page, page_size


def require_admin(x_terrax_admin_key: str | None = Header(default=None)) -> None:
    if settings.terrax_admin_api_key:
        if not x_terrax_admin_key or not secrets.compare_digest(
            x_terrax_admin_key, settings.terrax_admin_api_key
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid admin key")
    elif not settings.terrax_allow_anonymous_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "admin endpoints are disabled. Set TERRAX_ADMIN_API_KEY, or "
                "TERRAX_ALLOW_ANONYMOUS_ADMIN=true for a local demo."
            ),
        )


def audit(
    db: Session,
    *,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor: str = "anonymous",
    details: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
    )


__all__ = ["get_db", "pagination", "require_admin", "audit"]
