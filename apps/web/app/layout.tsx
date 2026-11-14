import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import "./globals.css";

export const metadata: Metadata = {
  title: "SNSX SPACE · Planetary analogue intelligence",
  description:
    "NASA Space Apps Challenge 2026: Identify Earth Locations that Analog the Permanent Moon Base Locations and Mars. SNSX SPACE uses authoritative NASA, USGS, ESA, and Copernicus data with transparent similarity, separate confidence, and inspectable provenance.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Browser extensions can inject attributes (for example data-qb-installed) into <html>
    // before React hydrates. Suppress only that element's attribute mismatch.
    <html lang="en" suppressHydrationWarning>
      <body>
        <Nav />
        {children}
        <Footer />
      </body>
    </html>
  );
}
