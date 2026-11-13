export function Footer() {
  return (
    <footer className="footer">
      <div className="shell footer-row">
        <div>
          <b style={{ color: "var(--muted)", letterSpacing: "0.1em" }}>TERRA-X</b>
          <br />
          Similarity is a bounded heuristic, not a probability or a landing-safety assessment.
          <div className="tiny faint" style={{ marginTop: 8 }}>
            Independent research prototype. Not an official NASA product; not affiliated with or
            endorsed by NASA.
          </div>
        </div>
        <div style={{ maxWidth: 560 }}>
          Source products: NASA LRO LOLA, MGS MOLA, LRO WAC, NASA GIBS, ESA Copernicus DEM,
          ESA WorldCover, Sentinel-2, OpenStreetMap. Every number is stored with its product,
          method, footprint, and version.
        </div>
      </div>
    </footer>
  );
}
