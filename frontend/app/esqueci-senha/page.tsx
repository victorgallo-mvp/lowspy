"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { esqueciSenha } from "@/lib/api";

export default function EsqueciSenhaPage() {
  const [email, setEmail] = useState("");
  const [enviado, setEnviado] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const enviar = useCallback(async () => {
    if (loading || !email.trim()) return;
    setErro(null);
    setLoading(true);
    try {
      await esqueciSenha(email.trim());
      setEnviado(true);
    } catch (e) {
      setErro(e instanceof Error ? e.message : "falha ao enviar");
    } finally {
      setLoading(false);
    }
  }, [email, loading]);

  return (
    <div className="loginwrap">
      <form
        className="logincard"
        onSubmit={(e) => {
          e.preventDefault();
          enviar();
        }}
      >
        <h1>Esqueci minha senha</h1>
        {enviado ? (
          <p className="loginlink">
            se esse e-mail tiver uma conta, um link pra redefinir a senha foi enviado. Confira
            sua caixa de entrada (e o spam).
          </p>
        ) : (
          <>
            <label>
              e-mail
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
            </label>
            {erro && <p className="loginerro">{erro}</p>}
            <button type="submit" disabled={loading || !email.trim()}>
              {loading ? "enviando…" : "enviar link de redefinição"}
            </button>
          </>
        )}
        <p className="loginlink">
          <Link href="/login">voltar pro login</Link>
        </p>
      </form>
    </div>
  );
}
