"use client";

import { useEffect, useState } from "react";
import { getUsuarios, UsuarioAdmin } from "@/lib/api";

const fmtData = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" }) : "—";

export default function UsuariosPage() {
  const [usuarios, setUsuarios] = useState<UsuarioAdmin[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getUsuarios().then(setUsuarios).catch(() => setErr("falha ao carregar"));
  }, []);

  if (err) return <div className="state err">{err}</div>;
  if (!usuarios) return <div className="state">carregando…</div>;

  return (
    <div className="usuarioswrap">
      <table className="usuariostabela">
        <thead>
          <tr>
            <th>e-mail</th>
            <th>plano</th>
            <th>tipo</th>
            <th>status</th>
            <th>cadastro</th>
          </tr>
        </thead>
        <tbody>
          {usuarios.map((u) => (
            <tr key={u.id}>
              <td>{u.email}</td>
              <td><span className={`planotag planotag--${u.plano}`}>{u.plano}</span></td>
              <td>{u.is_admin ? "admin" : "cliente"}</td>
              <td>
                <span className={`statusdot statusdot--${u.ativo ? "on" : "off"}`} />
                {u.ativo ? "ativo" : "inativo"}
              </td>
              <td>{fmtData(u.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {usuarios.length === 0 && <div className="state">nenhum usuário cadastrado ainda.</div>}
    </div>
  );
}
