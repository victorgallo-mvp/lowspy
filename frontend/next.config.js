/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Same-origin (/api/*) pro backend (Railway): app/api/[...path]/route.ts faz o
  // repasse manualmente (código normal, com fetch()) — não usa rewrites() porque
  // essa feature passa pelo filtro de borda da Vercel, que bloqueou o domínio da
  // Railway com DNS_HOSTNAME_RESOLVED_PRIVATE mesmo sendo um IP público de verdade.
};
module.exports = nextConfig;
