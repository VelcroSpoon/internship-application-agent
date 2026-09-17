import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `next dev` binds localhost and refuses requests for its own client
  // resources from a host it does not recognise, which silently leaves the
  // page server-rendered and unhydrated. Opening the dashboard at 127.0.0.1
  // instead of localhost is an easy way to hit that, so allow both.
  // Development only; it has no effect on a production build.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
