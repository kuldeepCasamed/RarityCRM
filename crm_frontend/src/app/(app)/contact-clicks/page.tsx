"use client";
import { useQuery } from "@tanstack/react-query";
import { format, subDays } from "date-fns";
import { useState } from "react";
import { api } from "@/lib/api";

type Click = { date: string; channel: "whatsapp" | "call"; source: string; utm_campaign: string; utm_term: string; utm_content: string; clicks: number };
type CampaignLeads = { utm_campaign: string; utm_term: string; utm_content: string; leads: number };

const num = "px-3 py-2 text-right tabular-nums";
const SOURCES: [string, string][] = [["meta_ads", "Meta Ads"], ["website", "Website"], ["google_ads", "Google Ads"]];
const key = (r: { utm_campaign: string; utm_term: string; utm_content: string }) => [r.utm_campaign, r.utm_term, r.utm_content].join("\u0001");

export default function ContactClicksPage() {
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const [days, setDays] = useState(30);
  const to = format(new Date(), "yyyy-MM-dd"), from = format(subDays(new Date(), days - 1), "yyyy-MM-dd");
  const isManager = me?.role === "admin" || me?.role === "manager";

  const clicks = useQuery({
    queryKey: ["contact-clicks", days],
    enabled: !!me,
    queryFn: async () => (await api.get<{ results: Click[] }>("/analytics/contact-clicks/", { params: { from, to } })).data.results,
  });
  const leads = useQuery({
    queryKey: ["ad-campaigns", days],
    enabled: isManager,
    queryFn: async () => (await api.get<{ results: CampaignLeads[] }>("/analytics/campaigns/", { params: { from, to } })).data.results,
  });

  const rows = clicks.data ?? [];
  const sum = (f: (r: Click) => boolean) => rows.filter(f).reduce((t, r) => t + r.clicks, 0);

  // campaign -> ad set -> ad, with WhatsApp / Call counts (+ form leads for managers)
  const table = new Map<string, { utm_campaign: string; utm_term: string; utm_content: string; whatsapp: number; call: number; leads: number }>();
  for (const r of rows) {
    if (!r.utm_campaign) continue;
    const k = key(r);
    const row = table.get(k) ?? { utm_campaign: r.utm_campaign, utm_term: r.utm_term, utm_content: r.utm_content, whatsapp: 0, call: 0, leads: 0 };
    row[r.channel] += r.clicks;
    table.set(k, row);
  }
  for (const l of leads.data ?? []) {
    const k = key(l);
    const row = table.get(k) ?? { utm_campaign: l.utm_campaign, utm_term: l.utm_term, utm_content: l.utm_content, whatsapp: 0, call: 0, leads: 0 };
    row.leads = l.leads;
    table.set(k, row);
  }
  const campaignRows = [...table.values()].sort((a, b) => b.whatsapp + b.call + b.leads - (a.whatsapp + a.call + a.leads));
  const untagged = { whatsapp: sum((r) => r.channel === "whatsapp" && !r.utm_campaign), call: sum((r) => r.channel === "call" && !r.utm_campaign) };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Contact clicks</h1>
          <p className="text-sm text-stone-500">Anonymous WhatsApp and Call button taps on the website, by the ad that brought the visitor. No personal data is stored.</p>
        </div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="border rounded-md px-3 py-1.5 bg-white text-sm">
          {[7, 30].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-white border rounded-lg p-4"><div className="text-xs text-stone-500">WhatsApp clicks</div><div className="text-2xl font-semibold tabular-nums">{sum((r) => r.channel === "whatsapp")}</div></div>
        <div className="bg-white border rounded-lg p-4"><div className="text-xs text-stone-500">Call clicks</div><div className="text-2xl font-semibold tabular-nums">{sum((r) => r.channel === "call")}</div></div>
        {SOURCES.slice(0, 2).map(([s, label]) => (
          <div key={s} className="bg-white border rounded-lg p-4"><div className="text-xs text-stone-500">{label}</div>
            <div className="text-sm mt-1 tabular-nums">{sum((r) => r.source === s && r.channel === "whatsapp")} WhatsApp · {sum((r) => r.source === s && r.channel === "call")} Call</div></div>
        ))}
      </div>
      <div className="bg-white border rounded-lg px-4 py-2 text-sm text-stone-600">
        {SOURCES.slice(2).map(([s, label]) => <span key={s} className="mr-6">{label}: {sum((r) => r.source === s && r.channel === "whatsapp")} WhatsApp · {sum((r) => r.source === s && r.channel === "call")} Call</span>)}
        <span>Without a campaign tag: {untagged.whatsapp} WhatsApp · {untagged.call} Call</span>
      </div>

      <div className="bg-white border rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr>
              <th className="px-3 py-2">Campaign</th><th className="px-3 py-2">Ad set</th><th className="px-3 py-2">Ad</th>
              <th className={num}>WhatsApp</th><th className={num}>Call</th>{isManager && <th className={num}>Form leads</th>}
            </tr>
          </thead>
          <tbody>
            {clicks.isLoading && <tr><td className="p-3" colSpan={6}>Loading…</td></tr>}
            {campaignRows.map((r) => (
              <tr key={key(r)} className="border-t">
                <td className="px-3 py-2 font-medium">{r.utm_campaign}</td>
                <td className="px-3 py-2">{r.utm_term || "—"}</td>
                <td className="px-3 py-2">{r.utm_content || "—"}</td>
                <td className={num}>{r.whatsapp}</td><td className={num}>{r.call}</td>{isManager && <td className={num}>{r.leads}</td>}
              </tr>
            ))}
            {!clicks.isLoading && campaignRows.length === 0 && <tr><td className="p-6 text-center text-stone-400" colSpan={6}>No tagged ad clicks in this period</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
