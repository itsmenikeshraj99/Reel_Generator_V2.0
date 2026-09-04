/** @type {import('next').NextConfig} */

// Build the list of backend origins we need to allow in CSP. We can't read
// process.env at the network-request time, so we pass these through at build time.
const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const apiOrigin = new URL(apiUrl).origin;

const supabaseOrigin =
  process.env.NEXT_PUBLIC_SUPABASE_URL || "https://example.supabase.co";

const connectSrc = [
  "'self'",
  apiOrigin,
  "https://*.supabase.co",
  "wss://*.supabase.co",
  "https://*.googleapis.com",
  "ws://localhost:*", // HMR in dev
];

const mediaSrc = [
  "'self'",
  "blob:",
  "https://*.supabase.co",
  apiOrigin, // signed URLs come from the same origin as the API
];

const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // Next.js dev needs 'unsafe-eval' for fast-refresh; harmless in prod bundle.
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: https:",
      `media-src ${mediaSrc.join(" ")}`,
      `connect-src ${connectSrc.join(" ")}`,
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

const nextConfig = {
  reactStrictMode: true,
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "*.supabase.co" },
    ],
  },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

module.exports = nextConfig;
