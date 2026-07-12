# GARIMPO — dashboard (Next.js / Vercel)

Frontend do minerador: lista **rankeada** de produtos com score, badges de mercado/sinal,
filtros (mercado, sinal, score mín., preço máx.), painel de custo/dia e a **prova de demanda**
(comentários de intenção, sem nick por LGPD). Consome a API da Fase 3.

Design: terminal de sinais escuro (carvão + lima ácida = demanda confirmada, âmbar = vendedor
off-platform), Syne + IBM Plex Sans/Mono.

## Rodar local

```bash
cp .env.example .env.local     # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev                    # http://localhost:3000  (API rodando na 8000)
```

## Deploy — Vercel

1. Importe o repo no Vercel, **Root Directory = `frontend`**.
2. Env var: `NEXT_PUBLIC_API_URL = https://<sua-api>.up.railway.app`.
3. Deploy. No Railway, setar `CORS_ORIGINS = https://<seu-app>.vercel.app`.

Build validado com Next 14.2.35 (`npm run build` ✓ tipos ✓).
