import { NextRequest, NextResponse } from "next/server";

// Proxy same-origin manual (não usa next.config.js rewrites(), que passa pelo
// filtro de borda da Vercel e bloqueou o domínio da Railway com
// DNS_HOSTNAME_RESOLVED_PRIVATE mesmo resolvendo pra IP público de verdade).
// Código de aplicação comum (fetch()) não passa por esse filtro.
export const runtime = "nodejs"; // precisa do fetch/Headers completo do Node (não Edge)

const TARGET = process.env.API_PROXY_TARGET || "http://localhost:8000";

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  try {
    const url = `${TARGET}/${path.join("/")}${req.nextUrl.search}`;

    const headers = new Headers();
    req.headers.forEach((value, key) => {
      if (!["host", "connection", "content-length", "accept-encoding"].includes(key.toLowerCase())) {
        headers.set(key, value);
      }
    });
    // sem accept-encoding: o backend responde sem comprimir — evita todo o problema
    // abaixo na raiz (undici já descomprime sozinho, então repassar um Content-
    // Encoding que não bate mais com os bytes reais quebrava a decodificação
    // no navegador: "não foi possível decodificar os dados brutos")
    headers.set("accept-encoding", "identity");

    const hasBody = !["GET", "HEAD"].includes(req.method);
    const body = hasBody ? await req.arrayBuffer() : undefined;

    const upstream = await fetch(url, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
    });

    const resHeaders = new Headers();
    const STRIP = new Set(["set-cookie", "content-encoding", "content-length", "transfer-encoding"]);
    upstream.headers.forEach((value, key) => {
      if (!STRIP.has(key.toLowerCase())) resHeaders.set(key, value);
    });
    // vários Set-Cookie (sessão + csrf) — Headers.set() sobrescreveria um com o
    // outro, precisa anexar cada um individualmente. getSetCookie() nem sempre
    // existe dependendo do runtime — cai pro get() simples (1 cookie só) se faltar.
    const setCookies = typeof upstream.headers.getSetCookie === "function"
      ? upstream.headers.getSetCookie()
      : upstream.headers.get("set-cookie")
        ? [upstream.headers.get("set-cookie") as string]
        : [];
    for (const c of setCookies) resHeaders.append("set-cookie", c);

    const buf = await upstream.arrayBuffer();
    return new NextResponse(buf, { status: upstream.status, headers: resHeaders });
  } catch (e) {
    // "fetch failed" do undici é só o wrapper — a causa real vem em e.cause. Log
    // fica só no servidor (Vercel function logs); cliente recebe mensagem genérica.
    const cause = e instanceof Error && e.cause ? ` | cause: ${String(e.cause)}` : "";
    const msg = (e instanceof Error ? `${e.name}: ${e.message}` : String(e)) + cause;
    console.error("proxy falhou:", msg);
    return NextResponse.json({ detail: "não consegui falar com a API" }, { status: 502 });
  }
}

type Ctx = { params: { path: string[] } };

export async function GET(req: NextRequest, { params }: Ctx) {
  return proxy(req, params.path);
}
export async function POST(req: NextRequest, { params }: Ctx) {
  return proxy(req, params.path);
}
export async function PUT(req: NextRequest, { params }: Ctx) {
  return proxy(req, params.path);
}
export async function PATCH(req: NextRequest, { params }: Ctx) {
  return proxy(req, params.path);
}
export async function DELETE(req: NextRequest, { params }: Ctx) {
  return proxy(req, params.path);
}
