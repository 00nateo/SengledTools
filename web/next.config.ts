import type { NextConfig } from "next";

// The bulbs are driven by the Python side (../sengled_ctl.py api) on 127.0.0.1:8765.
// Proxy /api/* to it so the browser only ever talks to this app's origin.
const API = process.env.SENGLED_API ?? "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
