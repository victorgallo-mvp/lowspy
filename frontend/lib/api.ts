// Sempre relativo (/api) — o Next.js repassa pro backend real via rewrite
// (next.config.js, API_PROXY_TARGET) rodando servidor-a-servidor. O navegador só
// fala com o próprio domínio (mesma origem), então o cookie de sessão nunca é
// cross-site — funciona em qualquer navegador, inclusive Safari com ITP.
export const API_BASE = "/api";

// todo fetch pro backend precisa mandar o cookie de sessão (auth via cookie httpOnly)
const WITH_SESSION: RequestInit = { credentials: "include" };

// CSRF: o backend guarda um cookie legível (não httpOnly) com um token — toda
// chamada que muda estado (POST/DELETE) precisa ecoar esse valor no header
// X-CSRF-Token, senão o middleware do backend rejeita com 403. Um site atacante
// não consegue ler esse cookie (é do nosso domínio), só o nosso próprio JS.
function csrfHeader(): Record<string, string> {
  if (typeof document === "undefined") return {};
  const m = document.cookie.match(/(?:^|;\s*)lowspy_csrf=([^;]+)/);
  return m ? { "X-CSRF-Token": decodeURIComponent(m[1]) } : {};
}

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
    cta_tipo: "site" | "whatsapp" | null;
    cta_link: string | null;
  };
  score_componentes: {
    comment_score?: number;
    caption_score: number;
    n_comentarios_intencao?: number;
    densidade_intencao?: number;
    dias_ativos?: number;
  };
  comentarios_intencao: string[];
  feedback: { avaliacao: "positivo" | "negativo"; comentario: string | null } | null;
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

// --- Auth --------------------------------------------------------------------
export type Usuario = { id: number; email: string; is_admin: boolean };

export async function login(email: string, senha: string): Promise<Usuario> {
  const r = await fetch(`${API_BASE}/auth/login`, {
    ...WITH_SESSION,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, senha }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
  return r.json();
}

export async function registrar(email: string, senha: string): Promise<Usuario> {
  const r = await fetch(`${API_BASE}/auth/registro`, {
    ...WITH_SESSION,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, senha }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
  return r.json();
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { ...WITH_SESSION, method: "POST" });
}

export async function esqueciSenha(email: string): Promise<void> {
  // sempre 200 (o backend nunca vaza se o e-mail existe ou não) — sem try/catch
  // de detail aqui, só propaga erro de rede de verdade
  const r = await fetch(`${API_BASE}/auth/esqueci-senha`, {
    ...WITH_SESSION,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
}

export async function resetarSenha(token: string, novaSenha: string): Promise<void> {
  const r = await fetch(`${API_BASE}/auth/resetar-senha`, {
    ...WITH_SESSION,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, nova_senha: novaSenha }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
}

export async function getMe(): Promise<Usuario | null> {
  const r = await fetch(`${API_BASE}/auth/eu`, { ...WITH_SESSION, cache: "no-store" });
  if (!r.ok) return null;
  return r.json();
}

export class ApiError extends Error {
  constructor(public code: number) {
    super(`API ${code}`);
  }
}

async function _get(path: string): Promise<Response> {
  const r = await fetch(`${API_BASE}${path}`, { ...WITH_SESSION, cache: "no-store" });
  if (!r.ok) throw new ApiError(r.status);
  return r;
}

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
  return (await _get(`/produtos?${q.toString()}`)).json();
}

export async function getVarreduras(): Promise<Varredura[]> {
  return (await (await _get("/varreduras")).json()).varreduras;
}

export async function getCusto(): Promise<CustoResp> {
  return (await _get("/custo/dia")).json();
}

export type RunSnapshot = {
  sobreviventes?: number;
  total_buscado?: number;
  n0_posts?: number;
  novos?: number;
  comment_fetches?: number;
  idioma_dropados?: number;
  fisico_dropados?: number;
  highticket_dropados?: number;
  nao_digital_dropados?: number;
  velhos_dropados?: number;
  vistos_pulados?: number;
  sem_texto_dropados?: number;
  servico_local_dropados?: number;
  curto_dropados?: number;
  longo_dropados?: number;
  termos_tentados?: number;
  termos_disponiveis?: number;
  termo_atual?: string;
  orcamento_usado?: number;
  orcamento_total?: number;
  creditos_gastos?: number | null;
  breadth?: Record<string, number>;
};

export type Run = {
  id: number;
  status: "queued" | "running" | "done" | "error" | "interrupted";
  mode: string;
  fonte: "tiktok" | "meta";
  summary: RunSnapshot | null;
  progress: RunSnapshot | null;
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
  fonte: "tiktok" | "meta" = "tiktok"
): Promise<{ run_id: number }> {
  const r = await fetch(`${API_BASE}/varredura?dry=${dry}&fonte=${fonte}`, {
    ...WITH_SESSION,
    method: "POST",
    headers: csrfHeader(),
  });
  if (!r.ok) throw new TriggerError(r.status); // 401 sem login, 403 sem role admin/csrf, 409 já rodando
  return r.json();
}

export async function getRun(id: number): Promise<Run> {
  return (await _get(`/varredura/${id}`)).json();
}

export type Reverso = {
  id?: number;
  fonte?: "tiktok" | "meta";
  url: string;
  legenda: string;
  hashtags_encontradas: string[];
  preco_detectado: string | null;
  autor: string;
  engajamento?: { views: number; curtidas: number; comentarios: number };
  comentarios_lidos?: number;
  n_comentarios_intencao?: number;
  comentarios_intencao?: string[];
  sinal_legenda: string[];
  dias_ativos?: number | null;
  ativo?: boolean | null;
  digital_confirmado?: boolean;
  creditos_gastos: number | null;
  created_at?: string | null;
};

async function _analisarLink(path: string, url: string): Promise<Reverso> {
  const r = await fetch(`${API_BASE}${path}?url=${encodeURIComponent(url)}`, WITH_SESSION);
  if (!r.ok) {
    if (r.status === 401 || r.status === 403) throw new TriggerError(r.status);
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
  return r.json();
}

export const analisarLinkTiktok = (url: string) => _analisarLink("/reverso/tiktok", url);
export const analisarLinkMeta = (url: string) => _analisarLink("/reverso/meta", url);

export async function getReversoHistorico(fonte: "tiktok" | "meta" | "all" = "all"): Promise<Reverso[]> {
  return (await (await _get(`/reverso/historico?fonte=${fonte}`)).json()).historico;
}

export async function apagarReversoHistorico(id: number): Promise<void> {
  const r = await fetch(`${API_BASE}/reverso/historico/${id}`, {
    ...WITH_SESSION,
    method: "DELETE",
    headers: csrfHeader(),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
}

export async function getLatestRun(): Promise<{ running: boolean; ultima: Run | null }> {
  return _get("/varredura/status").then((r) => r.json());
}

export type TermoSugerido = {
  id: number;
  termo: string;
  fonte: "tiktok" | "meta" | "geral";
  nota: string;
  created_at: string | null;
};

export async function getTermosSugeridos(): Promise<TermoSugerido[]> {
  return (await (await _get("/termos-sugeridos")).json()).termos;
}

export async function criarTermoSugerido(
  termo: string,
  fonte: "tiktok" | "meta" | "geral",
  nota?: string
): Promise<TermoSugerido> {
  const r = await fetch(`${API_BASE}/termos-sugeridos`, {
    ...WITH_SESSION,
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify({ termo, fonte, nota: nota ?? "" }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
  return r.json();
}

export async function apagarTermoSugerido(id: number): Promise<void> {
  const r = await fetch(`${API_BASE}/termos-sugeridos/${id}`, {
    ...WITH_SESSION,
    method: "DELETE",
    headers: csrfHeader(),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
}

// --- Termos negativos (exclusão de post) --------------------------------------
export type TermoNegativo = {
  id: number;
  termo: string;
  fonte: "tiktok" | "meta" | "todas";
  origem: "manual" | "feedback";
  ativo: boolean;
  created_at: string | null;
};

export async function getTermosNegativos(): Promise<TermoNegativo[]> {
  return (await (await _get("/termos-negativos")).json()).termos;
}

export async function criarTermoNegativo(
  termo: string,
  fonte: "tiktok" | "meta" | "todas"
): Promise<TermoNegativo> {
  const r = await fetch(`${API_BASE}/termos-negativos`, {
    ...WITH_SESSION,
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify({ termo, fonte }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
  return r.json();
}

export async function apagarTermoNegativo(id: number): Promise<void> {
  const r = await fetch(`${API_BASE}/termos-negativos/${id}`, {
    ...WITH_SESSION,
    method: "DELETE",
    headers: csrfHeader(),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
}

// --- Feedback (avaliação humana de produto) -----------------------------------
export async function enviarFeedback(
  postId: string,
  avaliacao: "positivo" | "negativo",
  comentario?: string
): Promise<void> {
  const r = await fetch(`${API_BASE}/produtos/${postId}/feedback`, {
    ...WITH_SESSION,
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify({ avaliacao, comentario: comentario ?? "" }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(body?.detail || `API ${r.status}`);
  }
}

export async function apagarFeedback(postId: string): Promise<void> {
  const r = await fetch(`${API_BASE}/produtos/${postId}/feedback`, {
    ...WITH_SESSION,
    method: "DELETE",
    headers: csrfHeader(),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
}

// --- Feed (área do assinante) -------------------------------------------------
export type FeedProduto = {
  post_id: string;
  fonte: "tiktok" | "meta";
  produto: string;
  preco: string | null;
  nicho: string | null;
  url: string;
  cover_url: string | null;
  sinal: string;
  novo: boolean;
};

export type FeedResp = {
  plano: string;
  limite: number;
  total_pool: number;
  produtos: FeedProduto[];
};

export async function getFeed(): Promise<FeedResp> {
  const r = await fetch(`${API_BASE}/feed`, { ...WITH_SESSION, cache: "no-store" });
  if (!r.ok) throw new ApiError(r.status);
  return r.json();
}

// --- Admin: usuários + visão geral --------------------------------------------
export type UsuarioAdmin = {
  id: number;
  email: string;
  plano: string;
  is_admin: boolean;
  ativo: boolean;
  created_at: string | null;
};

export async function getUsuarios(): Promise<UsuarioAdmin[]> {
  return (await (await _get("/admin/usuarios")).json()).usuarios;
}

export type OverviewResp = {
  total_posts: number;
  posts_por_fonte: Record<string, number>;
  total_produtos: number;
  produtos_por_fonte: Record<string, number>;
  breadth_mercado: Record<string, number>;
  crescimento_14d: [string, number][];
  usuarios_por_plano: Record<string, number>;
  total_usuarios: number;
  ultimas_varreduras: Record<string, { id: number; status: string; finished_at: string | null }>;
};

export async function getOverview(): Promise<OverviewResp> {
  return _get("/admin/overview").then((r) => r.json());
}
