/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  eslint: {
    // Lint is run separately in CI via `pnpm lint`; do not fail the build on it.
    ignoreDuringBuilds: false,
  },
  async rewrites() {
    const backendHost =
      process.env.BACKEND_URL || 'https://Ajvex-insightgpt.hf.space';
    return [
      {
        source: '/api/v1/:path*',
        destination: `${backendHost}/api/v1/:path*`,
      },
      {
        source: '/status',
        destination: `${backendHost}/status`,
      },
      {
        source: '/health',
        destination: `${backendHost}/health`,
      },
    ];
  },
};

module.exports = nextConfig;

