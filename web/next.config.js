/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  eslint: {
    // Lint is run separately in CI via `pnpm lint`; do not fail the build on it.
    ignoreDuringBuilds: false,
  },
  async rewrites() {
    // Optional dev convenience: proxy same-origin `/api/*` calls to the backend
    // so the browser avoids CORS during local development. The typed client in
    // `lib/api.ts` uses NEXT_PUBLIC_API_URL directly, so this is only a fallback.
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiUrl) return [];
    return [
      {
        source: '/backend/:path*',
        destination: `${apiUrl}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
