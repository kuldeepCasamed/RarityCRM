import { NextResponse } from "next/server";

export const API_URL = process.env.CRM_API_URL ?? "http://localhost:8000/api/crm";
// Distinct name: cookies on "localhost" are shared across ports, so a generic name can collide with other local apps.
export const COOKIE = "rarity_crm_token";

// Serverless hosts (e.g. Netlify) cut functions off after ~10s, which is shorter than a free backend's cold start
// (~50s). Give up a little earlier ourselves so the user gets a clear message instead of a raw platform error.
const TIMEOUT_MS = Number(process.env.CRM_API_TIMEOUT_MS ?? 8000);

export const WAKING_MESSAGE =
  "The server is waking up (this can take up to a minute after a quiet period). Please try again shortly.";

/** fetch() to the Django backend with a timeout. Throws if the backend can't be reached in time. */
export function backendFetch(url: string, init: RequestInit = {}) {
  return fetch(url, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
}

export const wakingResponse = () => NextResponse.json({ detail: WAKING_MESSAGE, waking: true }, { status: 503 });
