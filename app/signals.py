"""Motor de sinais determinístico (Níveis 0–1). Funções puras — testadas em unit.

Descobertas do piloto embutidas aqui:
- N0 relativo por hashtag (não global) preserva vendedor de nicho.
- Dois sinais: intenção-em-comentário (forte p/ físico) + CTA-de-legenda (recupera
  vendedor digital cuja demanda é off-platform).
- Filtro de idioma pt-BR dropa hashtag global (inglês/espanhol/indonésio).
"""
from __future__ import annotations

import math
import re
from typing import Any

from .schemas import SearchItem

PRICE_RE = re.compile(r"(R\$\s?\d{1,4}(?:[.,]\d{2})?|\d{1,4}\s?reais)", re.IGNORECASE)
_PT_HINTS = re.compile(
    r"\b(você|voce|não|nao|com|para|meu|minha|grátis|gratis|apenas|preço|preco|"
    r"link na bio|compre|loja|fornecedor|receita|planilha|apostila|acesse|clique)\b",
    re.IGNORECASE,
)
_NONPT_HINTS = re.compile(
    r"\b(dinero|ganar|tienes|aquí|cómo|negocio|honesta|libros|the|your|you are|free|"
    r"how to|link in bio|money|rich|dans|pour|des beaux|ton premier|en bio|"
    r"yang|bisa|murah|untuk|semua|aplikasi|terbaru|kali|pieniadze)\b",
    re.IGNORECASE,
)


_FISICO = re.compile(
    r"\b(frete|correios|sedex|shopee|encomenda|enviamos|envio em|entrega em \d|"
    r"pronta entrega|sacolinha|em estoque|pedido m[íi]nimo|rastreio|transportadora|"
    r"envío|envio gratis|correo|paqueter[íi]a|contra entrega|"
    r"free shipping|shipping|ships in|tracking|in stock|out of stock)\b",
    re.IGNORECASE,
)


def is_fisico(text: str) -> bool:
    """Backstop anti-físico: marcadores de envio/estoque na legenda (só digital)."""
    return bool(_FISICO.search(text or ""))


# idioma: aceita pt/es/en (latino); dropa scripts não-latinos e línguas fora do escopo
_NONLATIN = re.compile(
    r"[Ѐ-ӿ؀-ۿऀ-ॿ฀-๿"
    r"一-鿿가-힯぀-ヿ]"  # cirílico, árabe, devanágari, tailandês, CJK, hangul, kana
)
_LANG_FORA = re.compile(
    r"\b(yang|bisa|murah|untuk|dengan|kalian|terbaru|banget|"  # indonésio
    r"pour|votre|avec|gratuit|t[ée]l[ée]charg|des beaux|"       # francês
    r"ücretsiz|indir|kostenlos|herunterladen)\b",              # turco/alemão
    re.IGNORECASE,
)


def lang_allowed(text: str) -> bool:
    """True p/ pt/es/en; False p/ script não-latino ou idioma fora do escopo."""
    t = text or ""
    if _NONLATIN.search(t):
        return False
    if _LANG_FORA.search(t):
        return False
    return True


def extract_price(*texts: str):
    for txt in texts:
        m = PRICE_RE.search(txt or "")
        if m:
            return m.group(0).strip()
    return None


def passes_level0_abs(item: SearchItem, cfg: dict) -> bool:
    t = cfg["thresholds"]
    st = item.statistics
    return st.comment_count >= t["abs_min_comments"] and st.digg_count >= t["abs_min_likes"]


def select_level0_relative(items: list[SearchItem], cfg: dict) -> list[SearchItem]:
    """Threshold relativo por hashtag: top-frac mais comentados, piso absoluto baixo."""
    t = cfg["thresholds"]
    floor = [it for it in items if passes_level0_abs(it, cfg)]
    if not floor:
        return []
    ranked = sorted(floor, key=lambda x: x.statistics.comment_count, reverse=True)
    keep = max(1, math.ceil(len(ranked) * t["relative_top_frac"]))
    return ranked[:keep]


def caption_seller_score(caption: str, cfg: dict) -> dict[str, Any]:
    """Sinal GRÁTIS de vendedor (CTA + comportamento + checkout na legenda)."""
    w = cfg["weights"]
    low = (caption or "").lower()
    score = 0.0
    hits: list[str] = []
    for gname, kws in (
        ("cta_legenda", cfg["keywords"]["cta_legenda"]),
        ("comportamento_venda", cfg["keywords"]["comportamento_venda"]),
        ("checkout", cfg["keywords"]["checkout"]),
    ):
        for kw in kws:
            if kw.lower() in low:
                score += w[gname]
                hits.append(kw)
                break
    return {"score": round(score, 2), "hits": hits}


def intent_score(comment_texts: list[str], caption: str, cfg: dict) -> dict[str, Any]:
    """Score ponderado de intenção nos comentários (peso, não match cru)."""
    w = cfg["weights"]
    groups = {
        "intencao": cfg["intencao"],
        "comportamento_venda": cfg["keywords"]["comportamento_venda"],
        "checkout": cfg["keywords"]["checkout"],
    }
    total = len(comment_texts)
    intent_comments = 0
    raw = 0.0
    matched: list[str] = []
    for text in comment_texts:
        low = text.lower()
        hit = 0.0
        for gname, words in groups.items():
            if any(kw.lower() in low for kw in words):
                hit += w[gname]
        if hit > 0:
            intent_comments += 1
            raw += hit
            matched.append(text)
    density = (intent_comments / total) if total else 0.0
    density_bonus = density * w["density_bonus_max"]
    return {
        "score": round(raw + density_bonus, 2),
        "n_comentarios_intencao": intent_comments,
        "n_comentarios_lidos": total,
        "densidade_intencao": round(density, 3),
        "matched_comments": matched,
    }


def classify_signal(intent: dict, cap: dict, cfg: dict) -> str:
    min_demand = cfg["weights"].get("min_intent_comments_for_demand", 2)
    if intent["n_comentarios_intencao"] >= min_demand:
        return "demanda_confirmada"
    if cap["score"] > 0 or intent["score"] > 0:
        return "vendedor_off_platform"
    return "sem_sinal"


def normalize_score(combined: float, cfg: dict) -> float:
    """Normaliza a DEMANDA 0-100 (auditável). Teto configurável evita saturar."""
    cap = cfg["thresholds"].get("score_norm_cap", 20.0)
    return round(min(100.0, 100.0 * combined / cap), 2)


def engagement_norm(views: int, likes: int, comments: int, cfg: dict) -> float:
    """Engajamento 0-100 em escala LOG (views vão de mil a milhões — sem log, um
    viral de 10M esmaga todo mundo). Pesos e referências no config."""
    s = cfg.get("score", {})
    pesos = s.get("pesos_engaj", {"views": 0.4, "likes": 0.3, "comentarios": 0.3})
    ref = s.get("ref_engaj", {"views": 1_000_000, "likes": 100_000, "comentarios": 5_000})

    def n(v, r):
        return min(1.0, math.log10(1 + max(0, v)) / math.log10(1 + max(1, r)))

    tot = (pesos["views"] + pesos["likes"] + pesos["comentarios"]) or 1
    combined = (
        pesos["views"] * n(views, ref["views"])
        + pesos["likes"] * n(likes, ref["likes"])
        + pesos["comentarios"] * n(comments, ref["comentarios"])
    )
    return round(100.0 * combined / tot, 2)


def final_score(demanda_norm: float, views: int, likes: int, comments: int,
                cfg: dict) -> "tuple[float, float]":
    """Combina DEMANDA (dominante) + engajamento. Retorna (score_final, engaj_norm)."""
    s = cfg.get("score", {})
    wd = s.get("peso_demanda", 0.7)
    we = s.get("peso_engajamento", 0.3)
    eng = engagement_norm(views, likes, comments, cfg)
    tot = (wd + we) or 1
    return round((wd * demanda_norm + we * eng) / tot, 2), eng


def is_high_ticket(text: str, cfg: dict) -> bool:
    """Dropa high-ticket (queremos low-ticket): mentoria/imersão/curso completo…"""
    low = (text or "").lower()
    return any(m.lower() in low for m in cfg.get("high_ticket", []))


def is_servico_local(text: str, cfg: dict) -> bool:
    """Dropa serviço local/saúde (ruído do Meta Ads): clínica, procedimento
    estético, hotel, médico — keyword genérica do PDF bate em qualquer
    pequeno negócio, não só infoproduto digital."""
    low = (text or "").lower()
    return any(m.lower() in low for m in cfg.get("servico_local", []))


def is_digital_confirmado(text: str, cfg: dict) -> bool:
    """Confirma que o anúncio É digital de fato (pdf/molde/planilha/download…).
    No Meta a keyword que achou o anúncio (preço, "Kit") é genérica demais
    sozinha; exige esse segundo sinal antes de aceitar."""
    low = (text or "").lower()
    termos = cfg.get("meta_ads", {}).get("confirmacao_digital", [])
    return any(t.lower() in low for t in termos)


# --------------------------------------------------------------------------- #
# Meta Ads (Facebook Ad Library) — sem comentário. Sinal de demanda é o TEMPO
# DE VEICULAÇÃO: anúncio que o anunciante mantém pagando sobreviveu ao teste
# do mercado (doc do operador: "mais de 15 dias ativos" = boa chance de venda).
# --------------------------------------------------------------------------- #
def meta_ativo_norm(dias_ativos: int, cfg: dict) -> float:
    """Normaliza tempo de veiculação 0-80 (satura em dias_ativos_ideal_max — a
    faixa "ideal" do doc — e não em algo maior). Deixa 20pts de headroom pro
    bônus de CTA/variações em meta_final_score; sem isso, conta antiga de anos
    (500-5000 dias) sempre bate o teto sozinha e todo mundo empata em 100."""
    ideal_max = cfg.get("meta_ads", {}).get("dias_ativos_ideal_max", 30)
    return round(min(80.0, 80.0 * max(0, dias_ativos) / max(1, ideal_max)), 2)


def meta_final_score(dias_ativos: int, collation_count: int, cap_score: float, cfg: dict) -> float:
    """Score Meta: tempo de veiculação domina (0-80) + bônus por variações testadas
    e CTA (0-20) — o bônus é o que diferencia anúncios que já saturaram o tempo ativo."""
    ativo_norm = meta_ativo_norm(dias_ativos, cfg)
    colat_bonus = min(10.0, max(0, collation_count) * 2.0)  # testar várias variações = escalando
    cta_bonus = min(10.0, cap_score * 3.0)
    return round(min(100.0, ativo_norm + colat_bonus + cta_bonus), 2)


def classify_signal_meta(dias_ativos: int, cap: dict, cfg: dict) -> str:
    thr = cfg.get("meta_ads", {}).get("dias_ativos_min", 15)
    if dias_ativos >= thr:
        return "anuncio_confirmado"
    if cap["score"] > 0:
        return "vendedor_off_platform"
    return "sem_sinal"
