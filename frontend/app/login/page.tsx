"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const entrar = useCallback(async () => {
    if (loading) return;
    setErro(null);
    setLoading(true);
    try {
      await login(email.trim(), senha);
      router.replace("/app");
    } catch (e) {
      setErro(e instanceof Error ? e.message : "falha ao entrar");
      setLoading(false);
    }
  }, [email, senha, loading, router]);

  return (
    <div className="loginwrap">
      <form
        className="logincard"
        onSubmit={(e) => {
          e.preventDefault();
          entrar();
        }}
      >
        <h1>Entrar</h1>
        <label>
          e-mail
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </label>
        <label>
          senha
          <input type="password" value={senha} onChange={(e) => setSenha(e.target.value)} />
        </label>
        {erro && <p className="loginerro">{erro}</p>}
        <button type="submit" disabled={loading}>
          {loading ? "entrando…" : "entrar"}
        </button>
        <p className="loginlink">
          <Link href="/esqueci-senha">esqueci minha senha</Link>
        </p>
        <p className="loginlink">
          ainda não tem conta? <Link href="/signup">criar conta</Link>
        </p>
      </form>
    </div>
  );
}
