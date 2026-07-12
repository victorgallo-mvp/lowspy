from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _normalize_db_url(url: str) -> str:
    """Railway/Heroku entregam postgres://... ; SQLAlchemy 2 + psycopg3 quer
    postgresql+psycopg://... . Normaliza sem quebrar SQLite."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# Sem DATABASE_URL cai em SQLite local (dev/testes). Prod (Railway) injeta Postgres.
DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL") or f"sqlite:///{ROOT / 'tiktok_miner.db'}")
SCRAPECREATORS_API_KEY = os.getenv("SCRAPECREATORS_API_KEY", "")
CREDIT_USD = float(os.getenv("CREDIT_USD", "0.002"))
MODEL_TIER2 = os.getenv("MODEL_TIER2", "claude-haiku-4-5")
# Origens permitidas do frontend (Vercel). "*" em dev; setar a URL do Vercel em prod.
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
FIXTURES = ROOT / "fixtures"


def load_config(path: Optional[Path] = None) -> dict:
    """Lexicon de score + thresholds + pricing (keywords de descoberta vêm do DB)."""
    p = path or (ROOT / "pilot_config.yaml")
    return yaml.safe_load(p.read_text("utf-8"))
