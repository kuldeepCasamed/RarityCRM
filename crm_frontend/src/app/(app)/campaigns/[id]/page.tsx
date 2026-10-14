"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import Link from "next/link";
import { use, useState } from "react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";

const STYLE: Record<string, string> = { draft: "bg-stone-100 text-stone-600", scheduled: "bg-sky-100 text-sky-700", sending: "bg-blue-100 text-blue-700", paused: "bg-amber-100 text-amber-700", sent: "bg-green-100 text-green-700", cancelled: "bg-red-100 text-red-700" };
const SKIP: Record<string, string> = { unsubscribed: "unsubscribed", bad_email: "bad email", no_email: "no email", deleted: "lead archived", cancelled: "cancelled" };

export default function CampaignDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const qc = useQueryClient();
  const [state, setState] = useState("");
  const { data: c } = useQuery({ queryKey: ["campaign", id], queryFn: async () => (await api.get<any>(`/campaigns/${id}/`)).data, refetchInterval: (q) => (["scheduled", "sending"].includes(q.state.data?.status) ? 15_000 : false) });
  const { data: r } = useQuery({ queryKey: ["campaign-report", id], queryFn: async () => (await api.get<any>(`/campaigns/${id}/report/`)).data, refetchInterval: 30_000 });
  const { data: rows } = useQuery({ queryKey: ["campaign-rows", id, state], queryFn: async () => (await api.get<any[]>(`/campaigns/${id}/recipients/`, { params: { state: state || undefined } })).data });
  const refresh = () => ["campaign", "campaign-report", "campaign-rows", "campaigns"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const act = useMutation({ mutationFn: (a: string) => api.post(`/campaigns/${id}/${a}/`), onSuccess: refresh, onError: (e) => toast.error(errMsg(e)) });
  if (!c || !r) return <p className="text-stone-500">Loading…</p>;
  const pct = (n: number) => (r.sent ? `${Math.round((n / r.sent) * 100)}%` : "—");
  const cards: [string, number, string?, string?][] = [
    ["Sent", r.sent], ["Delivered", r.delivered, pct(r.delivered)], ["Clicked", r.clicked, pct(r.clicked)], ["Replied", r.replied, pct(r.replied)],
    ["Booked afterwards", r.booked_after, pct(r.booked_after)], ["Opened ≈", r.opened, pct(r.opened), "Open counts are unreliable: many mail apps load images automatically or block them."],
    ["Bounced", r.bounced, pct(r.bounced)], ["Unsubscribed", r.unsubscribed, pct(r.unsubscribed)], ["Marked spam", r.complained],
  ];

  return (
    <div className="space-y-5 max-w-4xl">
      <div><Link href="/campaigns" className="text-xs text-stone-500">← Campaigns</Link></div>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{c.name}</h1>
        <span className={`text-xs rounded-full px-2 py-0.5 ${STYLE[c.status]}`}>{c.status}</span>
        <span className="ml-auto flex gap-2 text-sm">
          {["scheduled", "sending"].includes(c.status) && <button onClick={() => act.mutate("pause")} className="border rounded-md px-3 py-1.5 bg-white">Pause</button>}
          {c.status === "paused" && <button onClick={() => act.mutate("resume")} className="border rounded-md px-3 py-1.5 bg-white">Resume</button>}
          {["scheduled", "sending", "paused"].includes(c.status) && <button onClick={() => confirm(`Cancel "${c.name}"? Emails already sent stay sent; everyone still waiting is skipped.`) && act.mutate("cancel")} className="border border-red-400 text-red-600 rounded-md px-3 py-1.5 bg-white">Cancel</button>}
        </span>
      </div>
      <p className="text-sm text-stone-500">Subject: <b>{c.subject}</b> · by {c.created_by_name}{c.started_at ? ` · started ${format(new Date(c.started_at), "d MMM HH:mm")}` : c.scheduled_at ? ` · scheduled ${format(new Date(c.scheduled_at), "d MMM HH:mm")}` : ""}</p>

      <div>
        <div className="flex justify-between text-xs text-stone-500 mb-1"><span>{r.sent} of {r.recipients - r.skipped_total} sent{r.pending ? ` · ${r.pending} waiting` : ""}{r.failed ? ` · ${r.failed} failed` : ""}</span><span>{r.progress_pct}%</span></div>
        <div className="h-2 bg-stone-100 rounded-full overflow-hidden"><div className="h-full bg-brand" style={{ width: `${r.progress_pct}%` }} /></div>
        {r.skipped_total > 0 && <p className="text-xs text-stone-400 mt-1">Skipped {r.skipped_total}: {Object.entries(r.skipped).map(([k, n]) => `${n} ${SKIP[k] ?? k}`).join(", ")}</p>}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {cards.map(([label, n, p, tip]) => (
          <div key={label} className="bg-white border rounded-lg p-3" title={tip}>
            <div className="text-xs text-stone-500">{label}</div><div className="text-2xl font-semibold">{n}</div>{p && <div className="text-xs text-stone-400">{p} of sent</div>}
          </div>
        ))}
      </div>

      <section className="space-y-2">
        <div className="flex items-center gap-2"><h2 className="font-medium">People</h2>
          <select value={state} onChange={(e) => setState(e.target.value)} className="border rounded px-2 py-1 text-xs bg-white"><option value="">All</option><option value="sent">Sent</option><option value="pending">Waiting</option><option value="skipped">Skipped</option><option value="failed">Failed</option></select></div>
        <div className="bg-white border rounded-lg divide-y max-h-96 overflow-y-auto text-sm">
          {rows?.map((x) => (
            <div key={x.id} className="px-3 py-1.5 flex items-center gap-3">
              <Link href={`/leads/${x.lead}?tab=Emails`} className="text-brand w-40 truncate">{x.lead_name}</Link><span className="text-xs text-stone-400 w-48 truncate">{x.email}</span>
              <span className={x.state === "sent" ? "text-green-700" : x.state === "failed" ? "text-red-600" : "text-stone-500"}>{x.state === "skipped" ? `skipped: ${SKIP[x.skip_reason] ?? x.skip_reason}` : x.state}</span>
              {x.status && <span className="text-xs text-stone-400">{x.status}</span>}{x.clicked && <span className="text-xs text-green-700">clicked</span>}
              {x.sent_at && <span className="ml-auto text-xs text-stone-400">{format(new Date(x.sent_at), "d MMM HH:mm")}</span>}
            </div>
          ))}
          {rows?.length === 0 && <p className="p-4 text-sm text-stone-400">Nobody here.</p>}
        </div>
      </section>
    </div>
  );
}
