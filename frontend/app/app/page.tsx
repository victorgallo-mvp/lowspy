"use client";

import { useEffect, useState } from "react";
import { getFeed, FeedResp, ApiError } from "@/lib/api";

const SINAL_LABEL: Record<string, string> = {
  demanda_confirmada: "demanda confirmada",
  vendedor_off_platform: "vendedor ativo",
  anuncio_confirmado: "anúncio confirmado",
};

export default function FeedPage() {
  const [data, setData] = useState<FeedResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getFeed()
      .then(setData)
      .catch((e) => setErr(e instanceof ApiError ? `erro ${e.code}` : "falha ao carregar o feed"));
  }, []);

  return (
    <main className="feedwrap">
      <header className="feedhead">
        <h1>Seu feed de hoje</h1>
        {data && (
          <p className="feedsub">
            plano <b>{data.plano}</b> · {data.produtos.length} de {data.total_pool} produtos do dia
          </p>
        )}
      </header>

      {err ? (
        <div className="state err">{err}</div>
      ) : !data ? (
        <div className="state">carregando…</div>
      ) : data.produtos.length === 0 ? (
        <div className="state">nenhum produto disponível ainda hoje — volte mais tarde.</div>
      ) : (
        <div className="feedgrid">
          {data.produtos.map((p) => (
            <a key={p.post_id} className="feedcard" href={p.url} target="_blank" rel="noreferrer">
              {p.cover_url ? (
                <img src={p.cover_url} alt="" className="feedcover" />
              ) : (
                <div className="feedcover feedcover--vazia" />
              )}
              <div className="feedcardbody">
                {p.novo && <span className="feedbadge feedbadge--novo">novo</span>}
                <span className="feedbadge">{p.fonte === "meta" ? "Meta Ads" : "TikTok"}</span>
                <p className="feedtitulo">{p.produto}</p>
                <div className="feedmeta">
                  {p.preco && <span className="feedpreco">{p.preco}</span>}
                  {p.nicho && <span className="feednicho">{p.nicho}</span>}
                </div>
                {p.sinal && <span className="feedsinal">{SINAL_LABEL[p.sinal] || p.sinal}</span>}
              </div>
            </a>
          ))}
        </div>
      )}
    </main>
  );
}
