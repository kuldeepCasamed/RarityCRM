import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { COOKIE } from "@/lib/server";

const PUBLIC = ["/login", "/invite"];

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasToken = req.cookies.has(COOKIE);
  if (PUBLIC.some((p) => pathname.startsWith(p))) {
    if (pathname === "/login" && hasToken) return NextResponse.redirect(new URL("/dashboard", req.url));
    return NextResponse.next();
  }
  if (!hasToken) return NextResponse.redirect(new URL("/login", req.url));
  return NextResponse.next();
}

export const config = { matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"] };
