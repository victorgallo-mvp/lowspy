"use client";

import { useEffect, useState } from "react";
import { getOverview, OverviewResp } from "@/lib/api";

const compact = (n: number) =>
  new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 }).format(n || 0);
const fmtDia = (iso: string) =>
  iso === "sem_data" ? "?" : new Date(iso + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });

export default function VisaoGeralPage() {
  const [data, setData] = useState<OverviewResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getOverview().then(setData).catch(() => setErr("falha ao carregar"));
  }, []);

  if (err) return <div className="state err">{err}</div>;
  if (!data) return <div className="state">carregando…</div>;

  const maxBreadth = Math.max(1, ...Object.values(data.breadth_mercado));
  const maxGrowth = Math.max(1, ...data.crescimento_14d.map(([, n]) => n));

  return (
    <div className="overviewwrap">
      <div className="overviewgrid">
        <div className="overviewcard">
          <div className="k">produtos (total)</div>
          <div className="v">{compact(data.total_produtos)}</div>
          <div className="sub">
            {Object.entries(data.produtos_por_fonte).map(([f, n]) => `${f}: ${compact(n)}`).join(" · ")}
          </div>
        </div>
        <div className="overviewcard">
          <div className="k">posts vistos (total)</div>
          <div className="v">{compact(data.total_posts)}</div>
        </div>
        <div className="overviewcard">
          <div className="k">usuários ativos</div>
          <div className="v">{compact(data.total_usuarios)}</div>
          <div className="sub">
            {Object.entries(data.usuarios_por_plano).map(([p, n]) => `${p}: ${n}`).join(" · ") || "—"}
          </div>
        </div>
        {Object.entries(data.ultimas_varreduras).map(([fonte, run]) => (
          <div className="overviewcard" key={fonte}>
            <div className="k">última varredura · {fonte}</div>
            <div className="v" style={{ fontSize: 18 }}>
              <span className={`statusdot statusdot--${run.status === "done" ? "on" : "off"}`} />
              {run.status}
            </div>
            <div className="sub">{run.finished_at ? new Date(run.finished_at).toLocaleString("pt-BR") : "—"}</div>
          </div>
        ))}
      </div>

      <section className="overviewsection">
        <h2>crescimento de produtos (14 dias)</h2>
        <div className="growthbars">
          {data.crescimento_14d.map(([dia, n]) => (
            <div className="col" key={dia}>
              <div className="bar" style={{ height: `${(n / maxGrowth) * 80}px` }} />
              <span className="lbl">{fmtDia(dia)}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="overviewsection">
        <h2>distribuição por mercado (todos os produtos)</h2>
        <div className="breadthlist">
          {Object.entries(data.breadth_mercado)
            .sort((a, b) => b[1] - a[1])
            .map(([mercado, n]) => (
              <div className="breadthrow" key={mercado}>
                <span className="nome">{mercado}</span>
                <span className="bar"><i style={{ width: `${(n / maxBreadth) * 100}%` }} /></span>
                <span className="num">{n}</span>
              </div>
            ))}
        </div>
      </section>
    </div>
  );
}
