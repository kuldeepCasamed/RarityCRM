import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { API_URL, backendFetch, COOKIE, wakingResponse } from "@/lib/server";

// Same-origin proxy: browser never sees the token (httpOnly cookie -> Authorization header).
async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const token = (await cookies()).get(COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });

  const url = `${API_URL}/${path.join("/")}/${req.nextUrl.search}`;
  const headers: Record<string, string> = { Authorization: `Token ${token}` };
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await backendFetch(url, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
    });
  } catch {
    return wakingResponse(); // backend asleep / unreachable: tell the UI to retry rather than failing hard
  }

  const out = new NextResponse(upstream.body, { status: upstream.status });
  // A rejected token must not linger: otherwise /login bounces back to the app and the page reloads forever.
  if (upstream.status === 401) out.cookies.delete(COOKIE);
  for (const h of ["content-type", "content-disposition"]) {
    const v = upstream.headers.get(h);
    if (v) out.headers.set(h, v);
  }
  return out;
}

export { forward as GET, forward as POST, forward as PATCH, forward as PUT, forward as DELETE };
