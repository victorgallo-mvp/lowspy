import { NextRequest, NextResponse } from "next/server";

// Proxy same-origin manual (não usa next.config.js rewrites(), que passa pelo
// filtro de borda da Vercel e bloqueou o domínio da Railway com
// DNS_HOSTNAME_RESOLVED_PRIVATE mesmo resolvendo pra IP público de verdade).
// Código de aplicação comum (fetch()) não passa por esse filtro.
const TARGET = process.env.API_PROXY_TARGET || "http://localhost:8000";

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  const url = `${TARGET}/${path.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!["host", "connection", "content-length"].includes(key.toLowerCase())) {
      headers.set(key, value);
    }
  });

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const body = hasBody ? await req.arrayBuffer() : undefined;

  const upstream = await fetch(url, {
    method: req.method,
    headers,
    body,
    redirect: "manual",
  });

  const resHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (key.toLowerCase() !== "set-cookie") resHeaders.set(key, value);
  });
  // vários Set-Cookie (sessão + csrf) — Headers.set() sobrescreveria um com o
  // outro, precisa anexar cada um individualmente
  for (const c of upstream.headers.getSetCookie()) {
    resHeaders.append("set-cookie", c);
  }

  const buf = await upstream.arrayBuffer();
  return new NextResponse(buf, { status: upstream.status, headers: resHeaders });
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
