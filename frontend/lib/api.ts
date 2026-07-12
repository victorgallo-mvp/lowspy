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
  mercado: string;
  sinal: string;
  score: number;
  produto: string;
  preco: string | null;
  nicho: string | null;
  url: string;
  cover_url: string | null;
  engajamento: { curtidas: number; comentarios: number };
  score_componentes: {
    comment_score: number;
    caption_score: number;
    n_comentarios_intencao: number;
    densidade_intencao: number;
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

export type Filtros = {
  mercado?: string;
  sinal?: string;
  min_score?: number;
  preco_max?: number;
  limit?: number;
};

export async function getProdutos(f: Filtros): Promise<ProdutosResp> {
  const q = new URLSearchParams();
  if (f.mercado) q.set("mercado", f.mercado);
  if (f.sinal) q.set("sinal", f.sinal);
  if (f.min_score) q.set("min_score", String(f.min_score));
  if (f.preco_max) q.set("preco_max", String(f.preco_max));
  q.set("limit", String(f.limit ?? 50));
  const r = await fetch(`${API_BASE}/produtos?${q.toString()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
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

export async function triggerSweep(dry: boolean, token?: string): Promise<{ run_id: number }> {
  const r = await fetch(`${API_BASE}/varredura?dry=${dry}`, {
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

export async function getLatestRun(): Promise<{ running: boolean; ultima: Run | null }> {
  const r = await fetch(`${API_BASE}/varredura/status`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}
