import { NextResponse } from "next/server";
import { API_URL, backendFetch } from "@/lib/server";

// Public connectivity check: shows which backend host this site calls and whether it answers. No secrets.
export async function GET() {
  const origin = new URL(API_URL).origin;
  let backend: string | number = "unreachable";
  try {
    backend = (await backendFetch(`${origin}/healthz/`)).status;
  } catch {
    /* asleep or wrong host */
  }
  return NextResponse.json({ backendHost: new URL(API_URL).host, apiBase: API_URL, healthz: backend, configured: !!process.env.CRM_API_URL });
}
