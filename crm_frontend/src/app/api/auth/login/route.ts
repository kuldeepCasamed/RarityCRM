import { NextResponse } from "next/server";
import { API_URL, backendFetch, COOKIE, wakingResponse } from "@/lib/server";

export async function POST(req: Request) {
  const body = await req.json();
  let res: Response;
  try {
    res = await backendFetch(`${API_URL}/auth/login/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return wakingResponse();
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(data, { status: res.status });

  const out = NextResponse.json({ user: data.user });
  out.cookies.set(COOKIE, data.token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  });
  return out;
}
