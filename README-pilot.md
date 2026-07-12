# Piloto — Fase 0 (coleta + custo)

Script único e descartável que valida **coleta** e **custo** da mineração de
produtos low-ticket no TikTok orgânico via ScrapeCreators, aplicando a cascata:

- **Nível 0 (grátis):** metadado da busca `/v1/tiktok/search/top` — `comment_count > 20`,
  `digg_count >=`, região BR, recência ≤7d via `publish_time=this-week`.
- **Nível 1 (gate):** 1 página de `/v1/tiktok/video/comments` + regex de intenção ponderado.
- **Nível 2 (opcional, `--llm`):** Haiku 4.5 via Batch API extrai `{produto, preço, score, nicho}`.

O produto do piloto é o **relatório**: sobrevivência por nível, custo/1.000 posts
(créditos e USD), projeção do Nível 2, e exemplos com os comentários de intenção.

## Setup

```bash
python3.12 -m venv .venv        # (o sistema tem 3.9; 3.9 também roda o piloto)
./.venv/bin/pip install -r requirements-pilot.txt
cp .env.example .env            # preencha SCRAPECREATORS_API_KEY (e ANTHROPIC_API_KEY p/ --llm)
```

## Uso

```bash
./.venv/bin/python pilot.py                 # dry-run (fixtures, gasto ZERO) — padrão
./.venv/bin/python pilot.py --live          # enxuto (~50 créditos): 3 keywords × 1 pág
./.venv/bin/python pilot.py --live --llm     # + extração Haiku nos sobreviventes
```

Flags: `--keywords "pdf" "planilha"`, `--max-comment-fetches 30`, `--config outro.yaml`.

## Saídas

- `pilot_report.json` — relatório estruturado (máquina).
- `cost_log.jsonl` — 1 linha por chamada de API (endpoint, `credits_remaining`, ts).
  Precursor da tabela de custo da Fase 1.
- `pilot_run.log` — log do run.

## Notas

- **Config, não hardcode:** keywords (3 grupos), thresholds, pesos, caps e pricing
  ficam em `pilot_config.yaml`.
- **Dry-run** serve a mesma fixture para cada keyword → os exemplos aparecem duplicados;
  é artefato das fixtures, não bug. No `--live` cada query traz posts distintos.
- **Pricing** no yaml é placeholder (`credit_usd`, preços Haiku Batch) — ajustar ao plano real.
- Nada de Postgres/FastAPI/Celery nesta fase; interfaces (`SearchItem`, `Comment`,
  `passes_level0`, `intent_score`, clients) já ficam prontas pra Fase 1.
