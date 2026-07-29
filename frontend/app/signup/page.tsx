"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { registrar } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const criar = useCallback(async () => {
    if (loading) return;
    setErro(null);
    if (senha.length < 8) {
      setErro("senha precisa de pelo menos 8 caracteres");
      return;
    }
    setLoading(true);
    try {
      await registrar(email.trim(), senha);
      router.replace("/app");
    } catch (e) {
      setErro(e instanceof Error ? e.message : "falha ao criar conta");
      setLoading(false);
    }
  }, [email, senha, loading, router]);

  return (
    <div className="loginwrap">
      <form
        className="logincard"
        onSubmit={(e) => {
          e.preventDefault();
          criar();
        }}
      >
        <h1>Criar conta</h1>
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
          {loading ? "criando…" : "criar conta"}
        </button>
        <p className="loginlink">
          já tem conta? <Link href="/login">entrar</Link>
        </p>
      </form>
    </div>
  );
}
