import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { API_URL, backendFetch, COOKIE, wakingResponse } from "@/lib/server";

// Password change rotates the API token, so the cookie must be replaced.
export async function POST(req: Request) {
  const old = (await cookies()).get(COOKIE)?.value;
  if (!old) return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  let res: Response;
  try {
    res = await backendFetch(`${API_URL}/me/password/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Token ${old}` },
      body: await req.text(),
    });
  } catch {
    return wakingResponse();
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(data, { status: res.status });
  const out = NextResponse.json({ ok: true });
  out.cookies.set(COOKIE, data.token, {
    httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax", path: "/", maxAge: 60 * 60 * 24 * 7,
  });
  return out;
}
