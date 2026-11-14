"use client";

import { useMemo, useState } from "react";
import { Terrain3D } from "@/components/Terrain3D";

export interface TerrainTarget {
  id: string;
  label: string;
  body: string;
  lon: number;
  lat: number;
  sizeKm: number;
  group: string;
  detail: string;
}

const GROUP_ORDER = [
  "Earth candidate sites",
  "Moon reference regions",
  "Mars reference regions",
];

const BODY_FILTERS = [
  { id: "all", label: "All" },
  { id: "earth", label: "Earth" },
  { id: "moon", label: "Moon" },
  { id: "mars", label: "Mars" },
];

export function TerrainExplorer({ targets }: { targets: TerrainTarget[] }) {
  const [bodyFilter, setBodyFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);

  const counts = useMemo(() => {
    const next: Record<string, number> = { all: targets.length };
    for (const target of targets) {
      next[target.body] = (next[target.body] || 0) + 1;
    }
    return next;
  }, [targets]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return targets.filter((target) => {
      if (bodyFilter !== "all" && target.body !== bodyFilter) return false;
      if (!needle) return true;
      return (
        target.label.toLowerCase().includes(needle) ||
        target.detail.toLowerCase().includes(needle) ||
        target.group.toLowerCase().includes(needle)
      );
    });
  }, [targets, bodyFilter, query]);

  const groups = useMemo(() => {
    const map = new Map<string, TerrainTarget[]>();
    for (const target of filtered) {
      const rows = map.get(target.group) || [];
      rows.push(target);
      map.set(target.group, rows);
    }
    const ordered = GROUP_ORDER.filter((group) => map.has(group));
    for (const group of map.keys()) {
      if (!ordered.includes(group)) ordered.push(group);
    }
    return ordered.map((group) => ({ group, rows: map.get(group) || [] }));
  }, [filtered]);

  const active = filtered.find((target) => target.id === activeId) || filtered[0] || null;

  return (
    <div className="terrain-explorer">
      <aside className="panel terrain-index" aria-label="Terrain catalog">
        <div className="terrain-index-head">
          <div>
            <div className="eyebrow">Surface index</div>
            <h3>{filtered.length} of {targets.length}</h3>
          </div>
          <span className="chip idle">DEM</span>
        </div>

        <label className="terrain-search">
          <span className="sr-only">Search terrain surfaces</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search regions, coordinates, sites…"
          />
        </label>

        <div className="terrain-filters" role="tablist" aria-label="Filter by body">
          {BODY_FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              role="tab"
              aria-selected={bodyFilter === filter.id}
              className="terrain-filter"
              data-active={bodyFilter === filter.id}
              onClick={() => setBodyFilter(filter.id)}
            >
              {filter.label}
              <small>{counts[filter.id] || 0}</small>
            </button>
          ))}
        </div>

        <div className="terrain-index-list">
          {groups.length === 0 ? (
            <p className="terrain-index-empty">
              No surfaces match this filter. Clear the search or choose another body.
            </p>
          ) : (
            groups.map(({ group, rows }) => (
              <div key={group} className="terrain-group">
                <div className="terrain-group-head">
                  <span>{group}</span>
                  <small>{rows.length}</small>
                </div>
                {rows.map((target) => (
                  <button
                    key={target.id}
                    type="button"
                    className="terrain-entry"
                    data-active={active?.id === target.id}
                    onClick={() => setActiveId(target.id)}
                  >
                    <span className="terrain-entry-body">{target.body}</span>
                    <span className="terrain-entry-main">
                      <b>{target.label}</b>
                      <small>{target.detail}</small>
                    </span>
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </aside>

      <section className="panel terrain-stage" aria-live="polite">
        {active ? (
          <>
            <div className="terrain-stage-head">
              <div>
                <div className="eyebrow cyan">{active.group}</div>
                <h3>{active.label}</h3>
                <p>{active.detail}</p>
              </div>
              <span className="chip idle">{active.sizeKm} km window</span>
            </div>
            <Terrain3D
              key={active.id}
              body={active.body}
              lon={active.lon}
              lat={active.lat}
              sizeKm={active.sizeKm}
              label={`${active.label} 3D terrain`}
              className="explorer"
            />
            <div className="terrain-stage-foot">
              Every vertex is sampled from the authoritative DEM product shown above the legend.
              The NASA imagery drape is aligned to the same footprint and is display-only. Missing
              cells stay missing; the mesh never interpolates or invents elevation. Vertical
              exaggeration changes the display only and is not used in similarity scoring.
            </div>
          </>
        ) : (
          <div className="terrain-index-empty">
            Select a terrain surface to build its real elevation mesh.
          </div>
        )}
      </section>
    </div>
  );
}
