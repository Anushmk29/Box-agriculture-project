import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // The API enforces a 45-second work budget. Let it return its own result
  // before the web proxy closes an otherwise valid shipment confirmation.
  experimental: { proxyTimeout: 60_000 },
  async rewrites() {
    const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";
    return [{ source: "/backend/:path*", destination: `${backendOrigin}/:path*` }];
  },
};

export default nextConfig;
