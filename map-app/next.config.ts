import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The canonical map dataset lives at ../data/map_mvp/restaurants.json.
  turbopack: {
    root: path.join(__dirname, ".."),
  },
};

export default nextConfig;
