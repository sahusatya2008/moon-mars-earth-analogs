"""Idempotent catalog seed.

Run with ``python -m terrax.seed``. It inserts or refreshes the authoritative catalog,
planetary bodies, and preset reference regions. Re-running never duplicates rows and never
touches user-created regions, analyses, or reports.
"""

from __future__ import annotations

import logging

from shapely.geometry import box
from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog import CATALOG_VERSION, DATASETS, PLANETS, PRESET_REGIONS
from .connectors.raster import EARTH_RADIUS_KM, MARS_RADIUS_KM, MOON_RADIUS_KM, footprint_bbox
from .db import SessionLocal
from .models import DatasetCollection, PlanetaryBody, PlanetaryRegion

log = logging.getLogger("terrax.seed")

RADII = {"earth": EARTH_RADIUS_KM, "moon": MOON_RADIUS_KM, "mars": MARS_RADIUS_KM}

DATASET_FIELDS = (
    "title",
    "body_id",
    "source_agency",
    "mission",
    "instrument",
    "description",
    "variable_names",
    "access_protocol",
    "source_url",
    "documentation_url",
    "citation",
    "license",
    "license_url",
    "spatial_resolution_m",
    "temporal_resolution",
    "temporal_coverage",
    "processing_level",
    "requires_credentials",
    "credential_env_var",
    "status",
    "is_scoring_input",
)


def region_polygon(body_id: str, lon: float, lat: float, size_km: float) -> str:
    """EWKT polygon for a body-relative footprint.

    Storage is always SRID 4326 for interoperability; the explicit body_id on the row is the
    authority for interpreting the coordinates. Presets are chosen away from the seam.
    """
    west, south, east, north, crossing = footprint_bbox(lon, lat, size_km, RADII[body_id])
    if crossing:
        # Split at the antimeridian; MultiPolygon is not allowed by the column type, so use
        # the eastern part and let validation reject seam presets. The catalog avoids them.
        west = -180.0
    geom = box(max(-180.0, west), max(-90.0, south), min(180.0, east), min(90.0, north))
    return f"SRID=4326;{geom.wkt}"


def seed(db: Session | None = None) -> dict[str, int]:
    own = db is None
    session = db or SessionLocal()
    counts = {"planets": 0, "datasets": 0, "regions": 0}
    try:
        for planet in PLANETS:
            row = session.get(PlanetaryBody, planet["id"])
            if row is None:
                row = PlanetaryBody(id=planet["id"])
                session.add(row)
            for key in ("name", "radius_km", "description", "accent_color", "display_order"):
                setattr(row, key, planet[key])
            counts["planets"] += 1

        # Flush bodies first: datasets and regions carry an explicit FK but no ORM
        # relationship, so SQLAlchemy cannot infer insert ordering by itself.
        session.flush()

        for spec in DATASETS:
            row = session.get(DatasetCollection, spec["id"])
            if row is None:
                row = DatasetCollection(id=spec["id"])
                session.add(row)
            for key in DATASET_FIELDS:
                setattr(row, key, spec.get(key))
            if row.metadata_json is None:
                row.metadata_json = {}
            counts["datasets"] += 1

        session.flush()

        for preset in PRESET_REGIONS:
            row = session.scalar(
                select(PlanetaryRegion).where(
                    PlanetaryRegion.body_id == preset["body_id"],
                    PlanetaryRegion.name == preset["name"],
                    PlanetaryRegion.is_preset.is_(True),
                )
            )
            if row is None:
                row = PlanetaryRegion(body_id=preset["body_id"], name=preset["name"], is_preset=True)
                session.add(row)
            row.kind = preset.get("kind", "reference")
            row.description = preset.get("description")
            row.center_lon = preset["center_lon"]
            row.center_lat = preset["center_lat"]
            row.size_km = preset["size_km"]
            row.source = preset.get("source")
            row.source_url = preset.get("source_url")
            row.citation = preset.get("citation")
            row.geometry = region_polygon(
                preset["body_id"], preset["center_lon"], preset["center_lat"], preset["size_km"]
            )
            row.metadata_json = {"preset": True, "catalog_version": CATALOG_VERSION}
            counts["regions"] += 1

        session.commit()
        log.info("seed complete: %s", counts)
        return counts
    except Exception:
        session.rollback()
        raise
    finally:
        if own:
            session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(seed())


if __name__ == "__main__":
    main()
