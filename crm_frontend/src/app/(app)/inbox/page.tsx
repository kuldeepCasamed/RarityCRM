"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { api, errMsg, Paged } from "@/lib/api";

type Row = { id: number; lead: number | null; lead_name: string | null; assigned_to_name: string | null; subject: string; needs_reply: boolean; last_message_at: string; preview: string; from_address: string };
const TABS = [["needs_reply", "Needs reply"], ["unmatched", "Unmatched"], ["all", "All conversations"]] as const;

export default function InboxPage() {
  const qc = useQueryClient();
  const [kind, setKind] = useState<(typeof TABS)[number][0]>("needs_reply");
  const [mine, setMine] = useState(false);
  const { data } = useQuery({ queryKey: ["inbox", kind, mine], queryFn: async () => (await api.get<Paged<Row>>("/inbox/", { params: { kind, mine: mine ? 1 : undefined } })).data, refetchInterval: 60_000 });
  const { data: counts } = useQuery({ queryKey: ["inbox-count"], queryFn: async () => (await api.get<{ needs_reply: number; unmatched: number }>("/inbox/count/")).data });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["inbox"] }); qc.invalidateQueries({ queryKey: ["inbox-count"] }); };
  const resolve = useMutation({ mutationFn: (id: number) => api.post(`/email-threads/${id}/resolve/`), onSuccess: refresh });

  return (
    <div className="space-y-4 max-w-4xl">
      <h1 className="text-2xl font-semibold">Inbox</h1>
      <div className="flex flex-wrap items-center gap-2 border-b">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setKind(k)} className={`px-3 py-2 text-sm ${kind === k ? "border-b-2 border-brand text-brand font-medium" : "text-stone-500"}`}>
            {label}{k === "needs_reply" && !!counts?.needs_reply && <span className="ml-1 rounded-full bg-red-100 text-red-700 text-xs px-1.5">{counts.needs_reply}</span>}
            {k === "unmatched" && !!counts?.unmatched && <span className="ml-1 rounded-full bg-amber-100 text-amber-700 text-xs px-1.5">{counts.unmatched}</span>}
          </button>
        ))}
        <label className="ml-auto text-xs flex items-center gap-1 text-stone-500"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only my leads</label>
      </div>
      {kind === "unmatched" && <p className="text-sm text-stone-500">Replies we couldn't match to a lead (new sender, or the lead used a different address). Attach each one to the right lead.</p>}
      <div className="space-y-2">
        {data?.results.map((r) => (
          <div key={r.id} className="bg-white border rounded-lg p-3 text-sm space-y-1">
            <div className="flex items-center gap-3">
              {r.lead ? <Link href={`/leads/${r.lead}?tab=Emails`} className="font-medium text-brand">{r.lead_name}</Link> : <b className="text-stone-700">{r.from_address || "Unknown sender"}</b>}
              <span className="truncate text-stone-600">{r.subject}</span>
              <span className="ml-auto text-xs text-stone-400 shrink-0">{formatDistanceToNow(new Date(r.last_message_at), { addSuffix: true })}</span>
            </div>
            <div className="text-stone-500 truncate">{r.preview}</div>
            <div className="flex items-center gap-3 text-xs">
              {r.assigned_to_name && <span className="text-stone-400">{r.assigned_to_name}</span>}
              {r.lead && r.needs_reply && <button onClick={() => resolve.mutate(r.id)} className="border rounded px-2 py-0.5 bg-white">Mark handled</button>}
              {!r.lead && <Assign threadId={r.id} onDone={refresh} />}
            </div>
          </div>
        ))}
        {data?.results.length === 0 && <p className="text-stone-400 text-sm py-6 text-center">{kind === "needs_reply" ? "Nothing waiting for a reply. 🎉" : "Nothing here."}</p>}
      </div>
    </div>
  );
}

function Assign({ threadId, onDone }: { threadId: number; onDone: () => void }) {
  const [q, setQ] = useState("");
  const { data } = useQuery({ queryKey: ["assign-search", q], enabled: q.length >= 2, queryFn: async () => (await api.get<Paged<any>>("/leads/", { params: { q, page_size: 5 } })).data });
  const assign = useMutation({ mutationFn: (leadId: number) => api.post(`/email-threads/${threadId}/assign/`, { lead_id: leadId }), onSuccess: () => { toast.success("Attached to the lead"); onDone(); }, onError: (e) => toast.error(errMsg(e)) });
  return (
    <div className="space-y-1">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a lead to attach this to…" className="border rounded px-2 py-1 text-xs bg-white w-64" />
      {data?.results.map((l: any) => (
        <button key={l.id} onClick={() => assign.mutate(l.id)} className="block text-left text-xs border rounded px-2 py-1 bg-white hover:bg-stone-50 w-64">{l.name} <span className="text-stone-400">{l.email || l.phone}</span></button>
      ))}
    </div>
  );
}
