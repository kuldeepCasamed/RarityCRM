"use client";
import { useQuery } from "@tanstack/react-query";
import { format, subDays } from "date-fns";
import { useState } from "react";
import { api, decodeUtm } from "@/lib/api";

type Row = {
  utm_campaign: string; utm_term: string; utm_content: string;
  leads: number; new: number; consult_booked: number; treatment_booked: number; converted: number;
};

const num = "px-3 py-2 text-right tabular-nums";

export default function AdCampaignsPage() {
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const [days, setDays] = useState(30);
  const isManager = me?.role === "admin" || me?.role === "manager";
  const to = format(new Date(), "yyyy-MM-dd"), from = format(subDays(new Date(), days - 1), "yyyy-MM-dd");
  const { data, isLoading } = useQuery({
    queryKey: ["ad-campaigns", days],
    enabled: isManager,
    queryFn: async () => (await api.get<{ results: Row[] }>("/analytics/campaigns/", { params: { from, to } })).data,
  });

  if (me && !isManager) return <p className="text-stone-500">Ad campaigns is available to managers and admins.</p>;
  const merged = new Map<string, Row>();
  for (const r of data?.results ?? []) {
    const d = { ...r, utm_campaign: decodeUtm(r.utm_campaign), utm_term: decodeUtm(r.utm_term), utm_content: decodeUtm(r.utm_content) };
    const k = [d.utm_campaign, d.utm_term, d.utm_content].join("\u0001");
    const m = merged.get(k);
    if (!m) merged.set(k, d);
    else for (const f of ["leads", "new", "consult_booked", "treatment_booked", "converted"] as const) m[f] += d[f];
  }
  const rows = [...merged.values()];
  const total = rows.reduce(
    (t, r) => ({ leads: t.leads + r.leads, new: t.new + r.new, consult_booked: t.consult_booked + r.consult_booked, treatment_booked: t.treatment_booked + r.treatment_booked, converted: t.converted + r.converted }),
    { leads: 0, new: 0, consult_booked: 0, treatment_booked: 0, converted: 0 },
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Ad campaigns</h1>
          <p className="text-sm text-stone-500">Leads from tagged ad links, by campaign, ad set and ad. Counts show each lead&apos;s current stage.</p>
        </div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="border rounded-md px-3 py-1.5 bg-white text-sm">
          {[7, 30, 60, 90, 180, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>
      <div className="bg-white border rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr>
              <th className="px-3 py-2">Campaign</th><th className="px-3 py-2">Ad set</th><th className="px-3 py-2">Ad</th>
              <th className={num}>Leads</th><th className={num}>New</th><th className={num}>Consult booked</th><th className={num}>Treatment booked</th><th className={num}>Converted</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td className="p-3" colSpan={8}>Loading…</td></tr>}
            {rows.map((r, i) => (
              <tr key={i} className="border-t">
                <td className="px-3 py-2 font-medium">{r.utm_campaign}</td>
                <td className="px-3 py-2">{r.utm_term || "—"}</td>
                <td className="px-3 py-2">{r.utm_content || "—"}</td>
                <td className={num}>{r.leads}</td><td className={num}>{r.new}</td><td className={num}>{r.consult_booked}</td><td className={num}>{r.treatment_booked}</td><td className={num}>{r.converted}</td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && <tr><td className="p-6 text-center text-stone-400" colSpan={8}>No tagged ad leads in this period</td></tr>}
          </tbody>
          {rows.length > 0 && (
            <tfoot className="border-t bg-stone-50 font-medium">
              <tr><td className="px-3 py-2" colSpan={3}>Total</td><td className={num}>{total.leads}</td><td className={num}>{total.new}</td><td className={num}>{total.consult_booked}</td><td className={num}>{total.treatment_booked}</td><td className={num}>{total.converted}</td></tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
