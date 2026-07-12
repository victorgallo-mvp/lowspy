export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

export type Produto = {
  post_id: string;
  mercado: string;
  sinal: string;
  score: number;
  produto: string;
  preco: string | null;
  nicho: string | null;
  url: string;
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
