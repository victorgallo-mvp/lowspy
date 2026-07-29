"use client";

import { useEffect, useState, useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getMe, logout, Usuario } from "@/lib/api";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLoginPage = pathname === "/admin/login";
  const [checking, setChecking] = useState(!isLoginPage);
  const [usuario, setUsuario] = useState<Usuario | null>(null);

  useEffect(() => {
    if (isLoginPage) return;
    let ativo = true;
    getMe().then((u) => {
      if (!ativo) return;
      if (!u || !u.is_admin) {
        router.replace("/admin/login");
        return;
      }
      setUsuario(u);
      setChecking(false);
    });
    return () => {
      ativo = false;
    };
  }, [isLoginPage, router, pathname]);

  const sair = useCallback(async () => {
    await logout();
    router.replace("/admin/login");
  }, [router]);

  if (isLoginPage) return <>{children}</>;

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
        <span>admin · {usuario?.email}</span>
        <button onClick={sair}>sair</button>
      </div>
      {children}
    </>
  );
}
