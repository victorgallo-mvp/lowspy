"use client";

import { Suspense, useCallback, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { resetarSenha } from "@/lib/api";

function ResetarSenhaForm() {
  const router = useRouter();
  const token = useSearchParams().get("token") || "";
  const [novaSenha, setNovaSenha] = useState("");
  const [confirmar, setConfirmar] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sucesso, setSucesso] = useState(false);

  const salvar = useCallback(async () => {
    if (loading) return;
    setErro(null);
    if (novaSenha.length < 8) {
      setErro("a senha precisa de pelo menos 8 caracteres");
      return;
    }
    if (novaSenha !== confirmar) {
      setErro("as senhas não coincidem");
      return;
    }
    setLoading(true);
    try {
      await resetarSenha(token, novaSenha);
      setSucesso(true);
      setTimeout(() => router.replace("/login"), 2000);
    } catch (e) {
      setErro(e instanceof Error ? e.message : "falha ao redefinir");
    } finally {
      setLoading(false);
    }
  }, [token, novaSenha, confirmar, loading, router]);

  if (!token) {
    return (
      <div className="loginwrap">
        <div className="logincard">
          <h1>Link inválido</h1>
          <p className="loginlink">
            esse link de redefinição está incompleto. <Link href="/esqueci-senha">pedir um novo</Link>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="loginwrap">
      <form
        className="logincard"
        onSubmit={(e) => {
          e.preventDefault();
          salvar();
        }}
      >
        <h1>Nova senha</h1>
        {sucesso ? (
          <p className="loginlink">senha redefinida! redirecionando pro login…</p>
        ) : (
          <>
            <label>
              nova senha
              <input type="password" value={novaSenha} onChange={(e) => setNovaSenha(e.target.value)} autoFocus />
            </label>
            <label>
              confirmar nova senha
              <input type="password" value={confirmar} onChange={(e) => setConfirmar(e.target.value)} />
            </label>
            {erro && <p className="loginerro">{erro}</p>}
            <button type="submit" disabled={loading}>
              {loading ? "salvando…" : "redefinir senha"}
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

export default function ResetarSenhaPage() {
  return (
    <Suspense fallback={null}>
      <ResetarSenhaForm />
    </Suspense>
  );
}
