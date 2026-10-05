import { NextRequest, NextResponse } from "next/server";
import { API_URL, backendFetch, COOKIE, wakingResponse } from "@/lib/server";

// Public: invite lookup + acceptance (no auth cookie yet). Accepting logs the user in.
type Ctx = { params: Promise<{ token: string }> };

export async function GET(_req: NextRequest, ctx: Ctx) {
  const { token } = await ctx.params;
  try {
    const res = await backendFetch(`${API_URL}/team/accept-invite/${encodeURIComponent(token)}/`);
    return NextResponse.json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return wakingResponse();
  }
}

export async function POST(req: NextRequest, ctx: Ctx) {
  const { token } = await ctx.params;
  let res: Response;
  try {
    res = await backendFetch(`${API_URL}/team/accept-invite/${encodeURIComponent(token)}/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await req.text(),
    });
  } catch {
    return wakingResponse();
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(data, { status: res.status });

  const out = NextResponse.json({ ok: true }, { status: 201 });
  out.cookies.set(COOKIE, data.token, {
    httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 7,
  });
  return out;
}
