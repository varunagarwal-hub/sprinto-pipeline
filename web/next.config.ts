import type { NextConfig } from "next";

// A live operations dashboard: every page reads fresh rows from Neon at request time, and every
// server action revalidates the paths it touches. So no Cache Components / prerendered shells here.
const nextConfig: NextConfig = {
  cacheComponents: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
