"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format, subDays } from "date-fns";
import { useState } from "react";
import { toast } from "sonner";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errMsg, inr } from "@/lib/api";

type Row = {
  source: string; source_label: string; campaign: string; leads: number; contacted: number; consults: number; converted: number;
  hot: number; avg_score: number | null; spend: number; revenue: number; cost_per_lead: number | null; cost_per_consult: number | null;
  cost_per_conversion: number | null; conversion_rate: number | null; roas: number | null; roi_pct: number | null;
};
const SOURCES = ["website", "google_ads", "meta_ads", "whatsapp", "phone", "walk_in", "referral", "partner", "csv", "manual"];
const input = "border rounded-md px-3 py-1.5 bg-white text-sm";
const dash = (v: number | null, f: (n: number) => string = String) => (v === null || v === undefined ? "—" : f(v));

export default function AnalyticsPage() {
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const [days, setDays] = useState(30);
  const [groupBy, setGroupBy] = useState<"source" | "campaign">("source");
  const to = format(new Date(), "yyyy-MM-dd"), from = format(subDays(new Date(), days - 1), "yyyy-MM-dd");
  const roi = useQuery({
    queryKey: ["roi", days, groupBy],
    enabled: me?.role === "admin" || me?.role === "manager",
    queryFn: async () => (await api.get<{ rows: Row[]; total: Row }>("/analytics/source-roi/", { params: { from, to, group_by: groupBy } })).data,
  });

  if (me && me.role !== "admin" && me.role !== "manager") return <p className="text-stone-500">Analytics is available to managers and admins.</p>;
  const rows = roi.data?.rows ?? [];
  const chart = rows.map((r) => ({ name: groupBy === "campaign" ? `${r.source_label}${r.campaign ? " · " + r.campaign : ""}` : r.source_label, Spend: r.spend, Revenue: r.revenue }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <h1 className="text-2xl font-semibold">Source ROI</h1>
        <div className="flex gap-2">
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={input}>
            {[7, 30, 60, 90, 180, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as any)} className={input}>
            <option value="source">By source</option><option value="campaign">By campaign</option>
          </select>
        </div>
      </div>
      <p className="text-sm text-stone-500">
        Leads created {from} → {to}, with their conversions and revenue to date, against spend logged in the same period.
        Recent leads may not have converted yet, so short ranges understate ROI. "—" means no spend was recorded (not that it was free).
      </p>

      <div className="bg-white border rounded-lg overflow-x-auto">
        <table className="w-full text-sm whitespace-nowrap">
          <thead className="bg-stone-50 text-right text-stone-500">
            <tr>
              <th className="p-3 text-left">{groupBy === "campaign" ? "Source · campaign" : "Source"}</th>
              <th>Spend</th><th>Leads</th><th>Cost / lead</th><th>Consults</th><th>Converted</th><th>Conv. %</th>
              <th>Cost / conv.</th><th>Revenue</th><th>ROAS</th><th>ROI</th><th>Avg score</th>
            </tr>
          </thead>
          <tbody className="text-right">
            {rows.map((r, i) => (
              <tr key={i} className="border-t">
                <td className="p-3 text-left font-medium">{r.source_label}{r.campaign && <span className="text-stone-400 font-normal"> · {r.campaign}</span>}</td>
                <td>{r.spend ? inr(r.spend) : "—"}</td><td>{r.leads}</td><td>{dash(r.cost_per_lead, inr)}</td>
                <td>{r.consults}</td><td>{r.converted}</td><td>{dash(r.conversion_rate, (n) => n + "%")}</td>
                <td>{dash(r.cost_per_conversion, inr)}</td><td>{r.revenue ? inr(r.revenue) : "—"}</td>
                <td>{dash(r.roas, (n) => n + "×")}</td>
                <td className={r.roi_pct === null ? "" : r.roi_pct >= 0 ? "text-green-700 font-medium" : "text-red-600 font-medium"}>{dash(r.roi_pct, (n) => n + "%")}</td>
                <td className="pr-3">{dash(r.avg_score)}</td>
              </tr>
            ))}
            {roi.data && (
              <tr className="border-t-2 font-semibold bg-stone-50">
                <td className="p-3 text-left">Total</td><td>{inr(roi.data.total.spend)}</td><td>{roi.data.total.leads}</td><td>{dash(roi.data.total.cost_per_lead, inr)}</td>
                <td>{roi.data.total.consults}</td><td>{roi.data.total.converted}</td><td>{dash(roi.data.total.conversion_rate, (n) => n + "%")}</td>
                <td>{dash(roi.data.total.cost_per_conversion, inr)}</td><td>{inr(roi.data.total.revenue)}</td><td>{dash(roi.data.total.roas, (n) => n + "×")}</td>
                <td>{dash(roi.data.total.roi_pct, (n) => n + "%")}</td><td />
              </tr>
            )}
            {roi.isLoading && <tr><td colSpan={12} className="p-4 text-center text-stone-400">Loading…</td></tr>}
            {roi.data && rows.length === 0 && <tr><td colSpan={12} className="p-6 text-center text-stone-400">No leads or spend in this period</td></tr>}
          </tbody>
        </table>
      </div>

      {rows.length > 0 && (
        <div className="bg-white border rounded-lg p-4">
          <h2 className="font-medium mb-2">Spend vs revenue</h2>
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" fontSize={11} /><YAxis fontSize={11} tickFormatter={(v) => (v >= 1000 ? v / 1000 + "k" : v)} />
                <Tooltip formatter={(v) => inr(Number(v))} /><Legend />
                <Bar dataKey="Spend" fill="#a8a29e" radius={[4, 4, 0, 0]} /><Bar dataKey="Revenue" fill="#8c7864" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <SpendManager from={from} to={to} onChange={() => roi.refetch()} />
    </div>
  );
}

function SpendManager({ from, to, onChange }: { from: string; to: string; onChange: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["spend", from, to], queryFn: async () => (await api.get<{ results: any[] }>("/marketing-spend/", { params: { from, to, page_size: 200 } })).data.results });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["spend"] }); onChange(); };
  const add = useMutation({ mutationFn: (b: object) => api.post("/marketing-spend/", b), onSuccess: () => { toast.success("Spend added"); refresh(); }, onError: (e) => toast.error(errMsg(e)) });
  const del = useMutation({ mutationFn: (id: number) => api.delete(`/marketing-spend/${id}/`), onSuccess: refresh });
  const [importMsg, setImportMsg] = useState("");

  async function importCsv(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const { data: r } = await api.post<{ created: number; errors: { row: number; error: string }[] }>("/marketing-spend/import/", fd);
      setImportMsg(`${r.created} rows imported` + (r.errors.length ? `; ${r.errors.length} failed — ` + r.errors.slice(0, 5).map((x) => `row ${x.row}: ${x.error}`).join("; ") : ""));
      refresh();
    } catch (err) { setImportMsg(errMsg(err)); }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Marketing spend</h2>
      <form className="bg-white border rounded-lg p-3 flex flex-wrap gap-2 items-end" onSubmit={(e) => {
        e.preventDefault(); const f = new FormData(e.currentTarget);
        add.mutate({ date: f.get("date"), source: f.get("source"), campaign: f.get("campaign"), amount: f.get("amount"), notes: f.get("notes") });
        e.currentTarget.reset();
      }}>
        <label className="text-xs">Date<input name="date" type="date" required defaultValue={to} className={input + " block"} /></label>
        <label className="text-xs">Source<select name="source" defaultValue="google_ads" className={input + " block"}>{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></label>
        <label className="text-xs">Campaign (utm_campaign)<input name="campaign" placeholder="optional" className={input + " block"} /></label>
        <label className="text-xs">Amount (₹)<input name="amount" type="number" min="1" step="0.01" required className={input + " block w-32"} /></label>
        <label className="text-xs flex-1 min-w-32">Notes<input name="notes" className={input + " block w-full"} /></label>
        <button className="bg-brand text-white rounded-md px-4 py-1.5 text-sm">Add</button>
      </form>
      <div className="text-sm flex items-center gap-3">
        <label className="text-brand underline cursor-pointer">Import spend CSV<input type="file" accept=".csv" onChange={importCsv} className="hidden" /></label>
        <span className="text-stone-400">columns: date, source, amount, campaign, notes</span>
        {importMsg && <span className="text-stone-600">{importMsg}</span>}
      </div>
      <div className="bg-white border rounded-lg divide-y max-h-80 overflow-y-auto">
        {data?.map((s) => (
          <div key={s.id} className="p-2 px-3 text-sm flex items-center gap-3">
            <span className="w-24">{s.date}</span><span className="w-24">{s.source}</span><span className="text-stone-500 flex-1">{s.campaign} {s.notes && `· ${s.notes}`}</span>
            <b>{inr(Number(s.amount))}</b>
            <button onClick={() => confirm("Delete this spend entry?") && del.mutate(s.id)} className="text-red-600 text-xs">Delete</button>
          </div>
        ))}
        {data?.length === 0 && <p className="p-4 text-sm text-stone-400">No spend logged in this period. Add your ad spend to see cost per lead and ROI.</p>}
      </div>
    </section>
  );
}
