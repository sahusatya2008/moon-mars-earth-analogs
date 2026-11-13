import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import "./globals.css";

export const metadata: Metadata = {
  title: "TERRA-X · Planetary analogue intelligence",
  description:
    "Find Earth terrain analogues for Moon and Mars regions using authoritative NASA, USGS, ESA, and Copernicus data. Transparent similarity, separate confidence, inspectable provenance.",
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
