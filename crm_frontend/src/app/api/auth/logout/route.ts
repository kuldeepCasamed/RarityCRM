import { NextResponse } from "next/server";
import { COOKIE } from "@/lib/server";

export async function POST() {
  const out = NextResponse.json({ ok: true });
  out.cookies.delete(COOKIE);
  return out;
}
