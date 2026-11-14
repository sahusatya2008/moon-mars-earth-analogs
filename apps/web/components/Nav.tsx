"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/analyses", label: "Analyses" },
  { href: "/terrain", label: "3D terrain" },
  { href: "/datasets", label: "Data observatory" },
  { href: "/methodology", label: "Methodology" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="nav">
      <div className="shell nav-inner">
        <Link href="/" className="brand" aria-label="SNSX SPACE home">
          <span className="brand-mark" />
          SNSX SPACE
        </Link>
        <nav className="nav-links" aria-label="Primary">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="nav-link"
              data-active={pathname === link.href || pathname.startsWith(`${link.href}/`)}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="nav-status">
          <span className="dot" style={{ color: "var(--cyan)" }} />
          NASA Space Apps 2026
        </div>
      </div>
    </header>
  );
}
