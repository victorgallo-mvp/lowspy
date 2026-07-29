"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getMe, logout, Usuario } from "@/lib/api";

const ABAS = [
  { href: "/admin", label: "mineração" },
  { href: "/admin/visao-geral", label: "visão geral" },
  { href: "/admin/usuarios", label: "usuários" },
];

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
        <nav className="adminnav">
          {ABAS.map((a) => (
            <Link key={a.href} href={a.href} className={pathname === a.href ? "active" : ""}>
              {a.label}
            </Link>
          ))}
        </nav>
        <span className="adminbar-right">
          {usuario?.email}
          <button onClick={sair}>sair</button>
        </span>
      </div>
      {children}
    </>
  );
}
