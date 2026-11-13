"use client";

import { useState } from "react";
import { Globe, type GlobePin } from "@/components/Globe";
import type { BodyId, Planet } from "@/lib/types";

const SUBTITLES: Record<string, string> = {
  earth: "Candidate body · Copernicus GLO-30",
  moon: "Reference body · LRO LOLA 128 ppd",
  mars: "Reference body · MGS MOLA 463 m",
};

export function HeroGlobes({
  planets,
  pins,
}: {
  planets: Planet[];
  pins: Record<string, GlobePin[]>;
}) {
  const [active, setActive] = useState<BodyId>("earth");
  const ordered = ["earth", "moon", "mars"];
  const planet = planets.find((p) => p.id === active);

  return (
    <div className="globe-stage" aria-label="Interactive planetary globes">
      <div className="globe-corner">
        <b>{planet?.name ?? active}</b>
        <span>{SUBTITLES[active]}</span>
      </div>
      <Globe
        body={active}
        pins={pins[active] || []}
        interactive
        autoRotate
        className="globe-fill"
      />
      <div className="globe-tabs" role="tablist" aria-label="Select body">
        {ordered.map((body) => (
          <button
            key={body}
            type="button"
            role="tab"
            aria-selected={active === body}
            className="globe-tab"
            data-active={active === body}
            onClick={() => setActive(body as BodyId)}
          >
            {body}
          </button>
        ))}
      </div>
    </div>
  );
}
