"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { getMe, logout, Usuario } from "@/lib/api";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [usuario, setUsuario] = useState<Usuario | null>(null);

  useEffect(() => {
    let ativo = true;
    getMe().then((u) => {
      if (!ativo) return;
      if (!u) {
        router.replace("/login");
        return;
      }
      setUsuario(u);
      setChecking(false);
    });
    return () => {
      ativo = false;
    };
  }, [router]);

  const sair = useCallback(async () => {
    await logout();
    router.replace("/login");
  }, [router]);

  if (checking) {
    return (
      <div style={{ padding: 40, fontFamily: "monospace", color: "#888" }}>
        verificando sessão…
      </div>
    );
  }

  return (
    <>
      <div className="adminbar">
        <span>{usuario?.email}</span>
        <button onClick={sair}>sair</button>
      </div>
      {children}
    </>
  );
}
