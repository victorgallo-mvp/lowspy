"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CustoResp,
  Filtros,
  Produto,
  ProdutosResp,
  TriggerError,
  Varredura,
  getCusto,
  getLatestRun,
  getProdutos,
  getRun,
  getVarreduras,
  triggerSweep,
} from "@/lib/api";

const compact = (n: number) =>
  new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 }).format(n || 0);
const usd = (n: number) => `US$ ${n.toFixed(2)}`;
const fmtDate = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString("pt-BR", {
        day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
      })
    : "—";

/* ícones estilo TikTok (SVG inline) */
const IconHeart = () => (
  <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden>
    <path d="M12 21s-7.6-4.7-10-9.4C.4 8.3 2.1 5 5.6 5c2 0 3.3 1.1 4.4 2.6C11.1 6.1 12.4 5 14.4 5 17.9 5 19.6 8.3 22 11.6 19.6 16.3 12 21 12 21z" />
  </svg>
);
const IconComment = () => (
  <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden>
    <path d="M12 3C6.5 3 2 6.6 2 11c0 2.5 1.4 4.8 3.7 6.2-.2 1.2-.8 2.6-1.8 3.7 1.9-.2 3.7-1 5.1-2.1.9.2 1.9.2 3 .2 5.5 0 10-3.6 10-8S17.5 3 12 3z" />
  </svg>
);
const IconEye = () => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden>
    <path d="M12 5C6 5 2 12 2 12s4 7 10 7 10-7 10-7-4-7-10-7zm0 11.5A4.5 4.5 0 1112 7a4.5 4.5 0 010 9.5zM12 14a2 2 0 100-4 2 2 0 000 4z" />
  </svg>
);

function Row({ p, i }: { p: Produto; i: number }) {
  const [open, setOpen] = useState(false);
  const [imgOk, setImgOk] = useState(true);
  const comments = open ? p.comentarios_intencao : p.comentarios_intencao.slice(0, 2);
  return (
    <article className="row" style={{ animationDelay: `${i * 40}ms` }}>
      <div className="rank">
        <span className="hash">#</span>
        {String(i + 1).padStart(2, "0")}
      </div>

      <a className="thumb" href={p.url} target="_blank" rel="noreferrer" aria-label="abrir no tiktok">
        {p.cover_url && imgOk ? (
          <img src={p.cover_url} alt="" loading="lazy" onError={() => setImgOk(false)} />
        ) : (
          <span className="ph">▶</span>
        )}
      </a>

      <div className="main">
        <div className="title">{p.produto}</div>
        <div className="badges">
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
          <span title="views">
            <IconEye /> <b>{compact(p.engajamento.views)}</b>
          </span>
          <span title="curtidas">
            <IconHeart /> <b>{compact(p.engajamento.curtidas)}</b>
          </span>
          <span title="comentários">
            <IconComment /> <b>{compact(p.engajamento.comentarios)}</b>
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
  const [f, setF] = useState<Filtros>({ limit: 60, run: "latest" });
  const [data, setData] = useState<ProdutosResp | null>(null);
  const [custo, setCusto] = useState<CustoResp | null>(null);
  const [varreduras, setVarreduras] = useState<Varredura[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dry, setDry] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [sweepMsg, setSweepMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [pr, cu, vs] = await Promise.all([
        getProdutos(f),
        getCusto().catch(() => null),
        getVarreduras().catch(() => [] as Varredura[]),
      ]);
      setData(pr);
      if (cu) setCusto(cu);
      setVarreduras(vs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "falha ao carregar");
    } finally {
      setLoading(false);
    }
  }, [f]);

  useEffect(() => {
    load();
  }, [load]);

  const pollRun = useCallback(
    async (id: number) => {
      setSweeping(true);
      for (let i = 0; i < 900; i++) {
        try {
          const run = await getRun(id);
          if (run.status === "running" || run.status === "queued") {
            setSweepMsg(`varredura em andamento… (${run.mode})`);
          } else {
            setSweeping(false);
            if (run.status === "done") {
              const s = run.summary;
              setSweepMsg(`✓ ${s?.sobreviventes ?? 0} produtos · ${s?.creditos_gastos ?? "?"} créditos`);
              setF((v) => ({ ...v, run: String(id) })); // pula pra varredura nova
            } else if (run.status === "error") {
              setSweepMsg(`✗ erro: ${run.error ?? "desconhecido"}`);
            } else {
              setSweepMsg("varredura interrompida");
            }
            return;
          }
        } catch {
          setSweeping(false);
          setSweepMsg("perdi contato com a API");
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
      setSweeping(false);
    },
    [],
  );

  useEffect(() => {
    getLatestRun()
      .then((s) => {
        if (s.running && s.ultima) pollRun(s.ultima.id);
      })
      .catch(() => {});
  }, [pollRun]);

  const runSweep = useCallback(async () => {
    if (sweeping) return;
    setSweepMsg(null);
    const token =
      typeof window !== "undefined" ? localStorage.getItem("lowspy_token") ?? undefined : undefined;
    try {
      const { run_id } = await triggerSweep(dry, token);
      pollRun(run_id);
    } catch (e) {
      if (e instanceof TriggerError && e.code === 401) {
        const t = window.prompt("Token de disparo (TRIGGER_TOKEN da API):") ?? "";
        if (!t) return setSweepMsg("disparo cancelado (sem token)");
        localStorage.setItem("lowspy_token", t);
        try {
          const { run_id } = await triggerSweep(dry, t);
          pollRun(run_id);
        } catch {
          setSweepMsg("token rejeitado");
        }
      } else if (e instanceof TriggerError && e.code === 409) {
        setSweepMsg("já existe uma varredura em andamento");
      } else {
        setSweepMsg("falha ao disparar");
      }
    }
  }, [dry, sweeping, pollRun]);

  const set = (patch: Partial<Filtros>) => setF((v) => ({ ...v, ...patch }));
  const hoje = custo?.dias?.[custo.dias.length - 1];
  const maxDia = Math.max(1, ...(custo?.dias ?? []).map((d) => d.total_usd));

  return (
    <main className="shell">
      <header className="top">
        <div className="brand">
          <h1>
            Low<span className="dot">Spy</span>
          </h1>
          <span className="kicker">radar de demanda</span>
        </div>
        <p className="tagline">
          Infoprodutos <b>low-ticket</b> com demanda real, minerados do TikTok orgânico —
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

      <section className="actions">
        <button className="btn" onClick={runSweep} disabled={sweeping}>
          {sweeping ? "⣾ minerando…" : "◆ rodar varredura"}
        </button>
        <label className="dry">
          <input type="checkbox" checked={dry} onChange={(e) => setDry(e.target.checked)} />
          modo teste (dry-run, gasto zero)
        </label>
        {sweepMsg && <span className="runmsg">{sweepMsg}</span>}
      </section>

      {/* seletor de varredura */}
      <section className="runbar">
        <label>varredura</label>
        <select value={f.run ?? "latest"} onChange={(e) => set({ run: e.target.value })}>
          <option value="latest">última busca</option>
          {varreduras
            .filter((v) => v.n_produtos > 0)
            .map((v) => (
              <option key={v.id} value={String(v.id)}>
                {fmtDate(v.finished_at)} · {v.n_produtos} produtos
              </option>
            ))}
          <option value="all">todas (acumulado)</option>
        </select>
        <span className="runcount">
          {loading ? "carregando…" : <><b>{data?.total ?? 0}</b> produtos</>}
        </span>
      </section>

      {/* filtros de engajamento */}
      <section className="filters">
        <div className="grp">
          <label>views mín.</label>
          <input type="number" min={0} placeholder="—" value={f.min_views ?? ""}
            onChange={(e) => set({ min_views: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
        <div className="grp">
          <label>curtidas mín.</label>
          <input type="number" min={0} placeholder="—" value={f.min_likes ?? ""}
            onChange={(e) => set({ min_likes: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
        <div className="grp">
          <label>comentários mín.</label>
          <input type="number" min={0} placeholder="—" value={f.min_comments ?? ""}
            onChange={(e) => set({ min_comments: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
        <div className="grp">
          <label>preço máx (R$)</label>
          <input type="number" min={0} placeholder="—" value={f.preco_max ?? ""}
            onChange={(e) => set({ preco_max: e.target.value ? Number(e.target.value) : undefined })} />
        </div>
      </section>

      {err ? (
        <div className="state err">
          erro: {err} · confira se a API está no ar em{" "}
          <b>{process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}</b>
        </div>
      ) : loading && !data ? (
        <div className="state">carregando o radar…</div>
      ) : data && data.produtos.length === 0 ? (
        <div className="state">
          nenhum produto nessa busca. clique em <b>◆ rodar varredura</b> pra minerar.
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
