# TikTok Miner — Fase 1 (ingestão + storage + pipeline)

Minera o TikTok orgânico (via **ScrapeCreators**) pra achar produtos low-ticket com
demanda real, aplicando uma **cascata de custo crescente** e ranqueando por dois sinais.
`pilot.py` é a Fase 0 (validação, descartável). O pacote `app/` é a Fase 1.

## Arquitetura

```
app/
  config.py         # settings + carrega o lexicon/thresholds do pilot_config.yaml
  db.py             # engine SQLAlchemy (Postgres prod / SQLite dev+testes)
  models.py         # Keyword, Post, Comment, Score, Produto, CostLog
  schemas.py        # Pydantic (SearchItem, Comment) + normalizador do /hashtag
  signals.py        # N0/N1 puros: is_ptbr, select_level0_relative, intent_score,
                    #   caption_seller_score, classify_signal, normalize_score
  scrapecreators.py # conector Live + DryRun (callback de custo)
  seed_keywords.py  # semeia o DB de keywords {termo, mercado, sinal} do yaml
  pipeline.py       # varredura idempotente: keywords → N0 → N1 → storage + ranking
```

**DB de keywords `{termo, mercado, sinal}`** (a ideia do operador): a varredura lê os
termos ativos, agrupa por mercado e busca. O **sinal por mercado** vem do achado do piloto:
`físico/revenda → demanda` (intenção no comentário), `digital/criativo/nicho → vendedor`
(CTA na legenda, porque a demanda é off-platform).

## Cascata (custo crescente)

1. **N0 (grátis):** metadado da busca, threshold **relativo por hashtag** (preserva nicho).
2. **N0.5 (grátis):** score de vendedor na legenda → prioriza qual post recebe o fetch pago.
3. **N1 (pago):** 1 página de comentários (`trim=false`), intenção ponderada + cota por mercado.
4. **Score final 0-100** com componentes salvos separados (auditoria); ranqueado melhor-primeiro.
5. Idempotente (upsert por `aweme_id`/`cid`), dedup por autor, **log de custo por chamada**.

## Rodar

```bash
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env            # SCRAPECREATORS_API_KEY (+ DATABASE_URL p/ Postgres)

# Postgres (prod):
docker compose up -d            # sobe o Postgres; setar DATABASE_URL no .env

# Varredura (dry-run = fixtures, gasto zero; --live = pago):
./.venv/bin/python -m app.pipeline --seed            # semeia keywords + varre (dry-run)
./.venv/bin/python -m app.pipeline --live            # varredura paga
```

Sem `DATABASE_URL` cai em SQLite local (`tiktok_miner.db`) — bom pra dev/testes.

## Fase 2 — extração (Nível 2, Haiku 4.5 Batch)

`app/extract.py` roda o Haiku via Batch API (−50%) nos sobreviventes sem `produto`,
preenchendo `produto/preço/nicho`. Idempotente (só processa pendentes). Custo de token
vai pro `cost_log`.

```bash
./.venv/bin/python -m app.extract      # precisa ANTHROPIC_API_KEY
```

## Fase 3 — API (FastAPI)

`app/api.py` serve o dashboard e o custo. CORS via `CORS_ORIGINS` (setar a URL do Vercel).

| Endpoint | O quê |
|---|---|
| `GET /health` | healthcheck (Railway) |
| `GET /produtos?limit&min_score&mercado&sinal&preco_max` | lista **ranqueada** (melhor primeiro) com score, componentes auditáveis, preço e comentários de intenção (LGPD: sem nick) |
| `GET /produtos/{post_id}` | detalhe |
| `GET /custo/dia` | custo/dia: requests ScrapeCreators (≈1 crédito/req) + tokens Haiku, em USD |

```bash
./.venv/bin/uvicorn app.api:app --reload    # http://localhost:8000/docs
```

## Testes

```bash
./.venv/bin/python -m pytest -q      # 17 testes: sinais + pipeline + extração + API
```

## Deploy — Railway (backend + Postgres) + Vercel (frontend)

**Railway:**
1. Novo projeto → adicione o plugin **Postgres** (injeta `DATABASE_URL`; `postgres://` é
   normalizado p/ `postgresql+psycopg://` no `config.py`).
2. Serviço **web**: Railway detecta o `railway.json`/`Procfile` → `uvicorn app.api:app`
   (healthcheck `/health`). `init_db()` cria as tabelas no boot (sem migração manual).
3. Vars: `SCRAPECREATORS_API_KEY`, `ANTHROPIC_API_KEY`, `CORS_ORIGINS=https://seu-app.vercel.app`,
   `AUTO_SEED=1` (semeia o DB de keywords no 1º boot).
4. **Cron da varredura** (Railway Cron): comando `python -m app.pipeline --live`
   (ex.: diário). **Extração**: `python -m app.extract` (após a varredura).

**Vercel:** frontend do dashboard consome a API do Railway (`GET /produtos`, `/custo/dia`).
Setar `NEXT_PUBLIC_API_URL` (ou equivalente) apontando pra URL do Railway, e o domínio do
Vercel em `CORS_ORIGINS` no Railway.

## Futuras (ganchos prontos, não construídas)

- **Ad Library** (valida a demanda do digital, que é off-platform) · **re-varredura** (Celery,
  velocidade de engajamento no tempo) · **Scout (Opus)** que expande o DB de keywords a partir
  dos vencedores.
