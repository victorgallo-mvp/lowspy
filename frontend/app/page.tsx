"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CustoResp,
  Filtros,
  Produto,
  ProdutosResp,
  getCusto,
  getProdutos,
} from "@/lib/api";

const SIG: Record<string, { cor: string; label: string }> = {
  demanda_confirmada: { cor: "#cbf24e", label: "demanda confirmada" },
  vendedor_off_platform: { cor: "#f2a640", label: "vendedor · off-platform" },
};
const MERCADO: Record<string, string> = {
  fisico_revenda: "físico · revenda",
  digital_info: "digital · info",
  criativo: "criativo",
  nicho: "nicho",
};

const brl = (n: number) => n.toLocaleString("pt-BR");
const usd = (n: number) => `US$ ${n.toFixed(2)}`;

function sigOf(s: string) {
  return SIG[s] ?? { cor: "#676a58", label: s };
}

function Row({ p, i }: { p: Produto; i: number }) {
  const [open, setOpen] = useState(false);
  const sig = sigOf(p.sinal);
  const comments = open ? p.comentarios_intencao : p.comentarios_intencao.slice(0, 2);
  return (
    <article
      className="row"
      style={{ ["--sig" as string]: sig.cor, animationDelay: `${i * 45}ms` }}
    >
      <div className="rank">
        <span className="hash">#</span>
        {String(i + 1).padStart(2, "0")}
      </div>

      <div className="main">
        <div className="title">{p.produto}</div>
        <div className="badges">
          <span className="badge sig">{sig.label}</span>
          <span className="badge mkt">{MERCADO[p.mercado] ?? p.mercado}</span>
          {p.nicho && <span className="badge mkt">{p.nicho}</span>}
          {p.preco && (
            <span className="badge price">
              <b>{p.preco}</b>
            </span>
          )}
        </div>

        {p.comentarios_intencao.length > 0 && (
          <div className="proof">
            <span className="plabel">
              prova de demanda · {p.score_componentes.n_comentarios_intencao} comentários
            </span>
            {comments.map((c, k) => (
              <div className="quote" key={k}>
                <b>“{c}”</b>
              </div>
            ))}
            {p.comentarios_intencao.length > 2 && (
              <button className="morebtn" onClick={() => setOpen((v) => !v)}>
                {open ? "menos" : `+${p.comentarios_intencao.length - 2} comentários`}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="metrics">
        <div className="score">
          <div className="num">
            {p.score.toFixed(0)}
            <small>/100</small>
          </div>
          <div className="meter">
            <div className="fill" style={{ width: `${Math.min(100, p.score)}%` }} />
          </div>
        </div>
        <div className="eng">
          <span>
            ♥ <b>{brl(p.engajamento.curtidas)}</b>
          </span>
          <span>
            ✎ <b>{brl(p.engajamento.comentarios)}</b>
          </span>
        </div>
        <a className="linkout" href={p.url} target="_blank" rel="noreferrer">
          abrir no tiktok ↗
        </a>
      </div>
    </article>
  );
}

export default function Dashboard() {
  const [f, setF] = useState<Filtros>({ limit: 50, min_score: 0 });
  const [data, setData] = useState<ProdutosResp | null>(null);
  const [custo, setCusto] = useState<CustoResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [pr, cu] = await Promise.all([getProdutos(f), getCusto().catch(() => null)]);
      setData(pr);
      if (cu) setCusto(cu);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "falha ao carregar");
    } finally {
      setLoading(false);
    }
  }, [f]);

  useEffect(() => {
    load();
  }, [load]);

  const set = (patch: Partial<Filtros>) => setF((v) => ({ ...v, ...patch }));
  const hoje = custo?.dias?.[custo.dias.length - 1];
  const maxDia = Math.max(1, ...(custo?.dias ?? []).map((d) => d.total_usd));

  return (
    <main className="shell">
      <header className="top">
        <div className="brand">
          <h1>
            GARIMPO<span className="dot">.</span>
          </h1>
          <span className="kicker">radar de demanda</span>
        </div>
        <p className="tagline">
          Produtos <b>low-ticket</b> com demanda real, minerados do TikTok orgânico —
          rankeados pelo sinal que prova a saída.
        </p>
      </header>

      <section className="cost">
        <div className="today">
          <span className="lbl">custo hoje</span>
          <span className="val">
            {hoje ? usd(hoje.total_usd) : "US$ 0.00"}{" "}
            <small>{hoje ? `${hoje.scrape_requests} req` : ""}</small>
          </span>
        </div>
        <div className="bars">
          {(custo?.dias ?? []).slice(-16).map((d) => (
            <div
              key={d.dia}
              className="bar"
              title={`${d.dia} · ${usd(d.total_usd)}`}
              style={{ height: `${Math.max(8, (d.total_usd / maxDia) * 100)}%` }}
            >
              <span className="cap" />
            </div>
          ))}
        </div>
      </section>

      <section className="filters">
        <div className="grp">
          <label>mercado</label>
          <select value={f.mercado ?? ""} onChange={(e) => set({ mercado: e.target.value || undefined })}>
            <option value="">todos</option>
            <option value="fisico_revenda">físico · revenda</option>
            <option value="digital_info">digital · info</option>
            <option value="criativo">criativo</option>
            <option value="nicho">nicho</option>
          </select>
        </div>
        <div className="grp">
          <label>sinal</label>
          <select value={f.sinal ?? ""} onChange={(e) => set({ sinal: e.target.value || undefined })}>
            <option value="">todos</option>
            <option value="demanda_confirmada">demanda confirmada</option>
            <option value="vendedor_off_platform">vendedor off-platform</option>
          </select>
        </div>
        <div className="grp">
          <label>score mín.</label>
          <input
            type="number"
            min={0}
            max={100}
            value={f.min_score ?? 0}
            onChange={(e) => set({ min_score: Number(e.target.value) })}
          />
        </div>
        <div className="grp">
          <label>preço máx (R$)</label>
          <input
            type="number"
            min={0}
            placeholder="—"
            value={f.preco_max ?? ""}
            onChange={(e) => set({ preco_max: e.target.value ? Number(e.target.value) : undefined })}
          />
        </div>
        <div className="spacer" />
        <div className="count">
          {loading ? "minerando…" : <><b>{data?.total ?? 0}</b> produtos</>}
        </div>
      </section>

      {err ? (
        <div className="state err">
          erro: {err} · confira se a API está no ar em <b>{process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}</b>
        </div>
      ) : loading && !data ? (
        <div className="state">varrendo o garimpo…</div>
      ) : data && data.produtos.length === 0 ? (
        <div className="state">
          nenhum produto bateu os filtros. rode uma varredura: <b>python -m app.pipeline --live</b>
        </div>
      ) : (
        <div className="list">
          {data?.produtos.map((p, i) => (
            <Row key={p.post_id} p={p} i={i} />
          ))}
        </div>
      )}
    </main>
  );
}
