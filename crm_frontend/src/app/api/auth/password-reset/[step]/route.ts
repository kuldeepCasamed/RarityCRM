import { NextResponse } from "next/server";
import { API_URL, backendFetch, wakingResponse } from "@/lib/server";

// Public (no session): forwards the forgot-password steps to the backend.
const STEPS = new Set(["request", "confirm"]);

export async function POST(req: Request, { params }: { params: Promise<{ step: string }> }) {
  const { step } = await params;
  if (!STEPS.has(step)) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  let res: Response;
  try {
    res = await backendFetch(`${API_URL}/auth/password-reset/${step}/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await req.text(),
    });
  } catch {
    return wakingResponse();
  }
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
