/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Proxy same-origin (/api/*) pro backend (Railway) — evita cookie cross-site
  // (Vercel ≠ Railway = domínios diferentes de verdade). Navegador só fala com o
  // próprio domínio; o Next faz o repasse servidor-a-servidor por trás. Sem isso,
  // Safari (ITP) e navegadores com bloqueio de cookie de terceiro derrubam a sessão
  // mesmo com SameSite=None — CORS/SameSite não resolvem esse caso, só same-origin.
  async rewrites() {
    const target = process.env.API_PROXY_TARGET || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};
module.exports = nextConfig;
