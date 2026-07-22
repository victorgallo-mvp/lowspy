function resolveBase(): string {
  // tolera espaço/quebra-de-linha, barra final e falta de esquema (erros comuns de env var)
  let raw = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").trim();
  raw = raw.replace(/\/+$/, "");
  if (raw && !/^https?:\/\//i.test(raw)) raw = "https://" + raw;
  return raw;
}

export const API_BASE = resolveBase();

export type Produto = {
  post_id: string;
  fonte: "tiktok" | "meta";
  idioma: "pt" | "es_en";
  mercado: string;
  termo_origem: string;
  sinal: string;
  novo: boolean;
  score: number;
  produto: string;
  preco: string | null;
  nicho: string | null;
  url: string;
  cover_url: string | null;
  engajamento?: { curtidas: number; comentarios: number; views: number };
  meta?: {
    pagina: string;
    dias_ativos: number;
    variacoes_ativas: number;
    ativo: boolean;
    total_anuncios_anunciante: number | null;
    tem_mais_anuncios: boolean;
  };
  score_componentes: {
    comment_score?: number;
    caption_score: number;
    n_comentarios_intencao?: number;
    densidade_intencao?: number;
    dias_ativos?: number;
  };
  comentarios_intencao: string[];
};

export type ProdutosResp = { total: number; produtos: Produto[] };

export type CustoDia = {
  dia: string;
  scrape_requests: number;
  scrape_usd: number;
  haiku_usd: number;
  total_usd: number;
};
export type CustoResp = { credit_usd: number; dias: CustoDia[] };

export type Fonte = "tiktok" | "meta" | "all";
export type Idioma = "pt" | "es_en" | "all";

export type Filtros = {
  min_score?: number;
  min_views?: number;
  min_likes?: number;
  min_comments?: number;
  preco_max?: number;
  limit?: number;
  run?: string; // latest | all | <id>
  only_new?: boolean;
  fonte?: Fonte;
  idioma?: Idioma;
};

export type Varredura = {
  id: number;
  status: string;
  mode: string;
  fonte: "tiktok" | "meta";
  finished_at: string | null;
  n_produtos: number;
};

export async function getProdutos(f: Filtros): Promise<ProdutosResp> {
  const q = new URLSearchParams();
  if (f.min_score) q.set("min_score", String(f.min_score));
  if (f.min_views) q.set("min_views", String(f.min_views));
  if (f.min_likes) q.set("min_likes", String(f.min_likes));
  if (f.min_comments) q.set("min_comments", String(f.min_comments));
  if (f.preco_max) q.set("preco_max", String(f.preco_max));
  if (f.only_new) q.set("only_new", "true");
  q.set("run", f.run ?? "latest");
  q.set("limit", String(f.limit ?? 60));
  q.set("fonte", f.fonte ?? "all");
  q.set("idioma", f.idioma ?? "pt");
  const r = await fetch(`${API_BASE}/produtos?${q.toString()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}

export async function getVarreduras(): Promise<Varredura[]> {
  const r = await fetch(`${API_BASE}/varreduras`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return (await r.json()).varreduras;
}

export async function getCusto(): Promise<CustoResp> {
  const r = await fetch(`${API_BASE}/custo/dia`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}

export type Run = {
  id: number;
  status: "queued" | "running" | "done" | "error" | "interrupted";
  mode: string;
  fonte: "tiktok" | "meta";
  summary: {
    sobreviventes?: number;
    total_buscado?: number;
    creditos_gastos?: number | null;
    breadth?: Record<string, number>;
  } | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export class TriggerError extends Error {
  constructor(public code: number) {
    super(`trigger ${code}`);
  }
}

export async function triggerSweep(
  dry: boolean,
  token?: string,
  fonte: "tiktok" | "meta" = "tiktok"
): Promise<{ run_id: number }> {
  const r = await fetch(`${API_BASE}/varredura?dry=${dry}&fonte=${fonte}`, {
    method: "POST",
    headers: token ? { "X-API-Token": token } : {},
  });
  if (!r.ok) throw new TriggerError(r.status); // 401 token, 409 já rodando
  return r.json();
}

export async function getRun(id: number): Promise<Run> {
  const r = await fetch(`${API_BASE}/varredura/${id}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}

export type Reverso = {
  url: string;
  legenda: string;
  hashtags_encontradas: string[];
  preco_detectado: string | null;
  autor: string;
  engajamento: { views: number; curtidas: number; comentarios: number };
  comentarios_lidos: number;
  n_comentarios_intencao: number;
  comentarios_intencao: string[];
  sinal_legenda: string[];
  creditos_gastos: number | null;
};

export async function analisarLinkTiktok(url: string, token?: string): Promise<Reverso> {
  const r = await fetch(`${API_BASE}/reverso/tiktok?url=${encodeURIComponent(url)}`, {
    headers: token ? { "X-API-Token": token } : {},
  });
  if (!r.ok) {
    if (r.status === 401) throw new TriggerError(401);
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
  return r.json();
}

export async function getLatestRun(): Promise<{ running: boolean; ultima: Run | null }> {
  const r = await fetch(`${API_BASE}/varredura/status`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}
