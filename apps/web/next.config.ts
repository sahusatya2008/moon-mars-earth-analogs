import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "gibs.earthdata.nasa.gov" },
      { protocol: "https", hostname: "trek.nasa.gov" },
    ],
  },
};

export default nextConfig;
