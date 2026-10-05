import axios from "axios";
import { toast } from "sonner";

export const api = axios.create({ baseURL: "/api/crm" });

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 503 && err.response?.data?.waking && typeof window !== "undefined") {
      toast.info("Server is waking up…", { id: "server-waking", duration: 15000, description: "This can take up to a minute after a quiet period. Retrying automatically." });
    }
    if (err.response?.status === 401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
      // clear the cookie first, otherwise the route guard sends us straight back (redirect loop)
      fetch("/api/auth/logout", { method: "POST" }).finally(() => { window.location.href = "/login"; });
    }
    return Promise.reject(err);
  },
);

export const STATUSES = [
  ["new", "New"], ["contacted", "Contacted"], ["consult_booked", "Consult Booked"],
  ["consult_done", "Consult Done"], ["treatment_plan_sent", "Plan Sent"], ["negotiating", "Negotiating"],
  ["treatment_booked", "Treatment Booked"], ["converted", "Converted"],
  ["lost", "Lost"], ["not_interested", "Not Interested"], ["invalid", "Invalid"],
] as const;
export const statusLabel = (s: string) => STATUSES.find(([v]) => v === s)?.[1] ?? s;

export type Lead = {
  id: number; name: string; email: string; phone: string; country: string;
  status: string; source: string; priority: string; assigned_to: number | null;
  assigned_to_name: string | null; tags: string[]; treatment_interests: string[];
  is_international: boolean; last_contacted_at: string | null; next_followup_at: string | null;
  created_at: string; updated_at?: string; score?: number; score_band?: "hot" | "warm" | "cold"; score_breakdown?: { label: string; points: number }[]; chief_complaint?: string; budget_band?: string; insurance?: string;
  urgency?: string; landing_url?: string;
};
export type Paged<T> = { count: number; results: T[] };

export const TREATMENTS = [
  ["implants", "Dental Implants"], ["invisalign", "Invisalign / Aligners"], ["smile_design", "Smile Designing"],
  ["single_day", "Single-Day Dentistry"], ["root_canal", "Root Canal"], ["braces", "Braces"],
  ["whitening", "Whitening"], ["full_mouth", "Full Mouth Rehab"], ["pediatric", "Pediatric"], ["other", "Other"],
] as const;

export const errMsg = (e: any): string => {
  const d = e?.response?.data;
  if (!d) return "Something went wrong";
  if (typeof d === "string") return d;
  return d.detail ?? Object.entries(d).map(([k, v]) => `${k}: ${[v].flat().join(" ")}`).join(" · ");
};

// Fallback when a lead has no explicit timezone (IANA names).
const COUNTRY_TZ: Record<string, string> = {
  india: "Asia/Kolkata", "united kingdom": "Europe/London", uk: "Europe/London", "united arab emirates": "Asia/Dubai",
  uae: "Asia/Dubai", "united states": "America/New_York", usa: "America/New_York", canada: "America/Toronto",
  australia: "Australia/Sydney", singapore: "Asia/Singapore", germany: "Europe/Berlin", nigeria: "Africa/Lagos",
  kenya: "Africa/Nairobi", "south africa": "Africa/Johannesburg", bangladesh: "Asia/Dhaka", nepal: "Asia/Kathmandu",
  "sri lanka": "Asia/Colombo", iraq: "Asia/Baghdad", oman: "Asia/Muscat", qatar: "Asia/Qatar",
};
export const leadTimezone = (l: { timezone?: string; country?: string }) =>
  l.timezone || COUNTRY_TZ[(l.country ?? "").trim().toLowerCase()] || "";

export function dualTime(iso: string, leadTz: string, repTz = "Asia/Kolkata") {
  const f = (tz: string) =>
    new Intl.DateTimeFormat("en-GB", { timeZone: tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
  try { return { rep: f(repTz), lead: leadTz && leadTz !== repTz ? f(leadTz) : null }; } catch { return { rep: f(repTz), lead: null }; }
}

export const inr = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : "₹" + Math.round(n).toLocaleString("en-IN");
