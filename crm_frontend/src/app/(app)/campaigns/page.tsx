"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import CampaignEditor from "@/components/CampaignEditor";
import { api, errMsg } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  draft: "bg-stone-100 text-stone-600", scheduled: "bg-sky-100 text-sky-700", sending: "bg-blue-100 text-blue-700",
  paused: "bg-amber-100 text-amber-700", sent: "bg-green-100 text-green-700", cancelled: "bg-red-100 text-red-700",
};

export default function CampaignsPage() {
  const qc = useQueryClient();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const isManager = me?.role === "admin" || me?.role === "manager";
  const { data } = useQuery({ queryKey: ["campaigns"], enabled: isManager, queryFn: async () => (await api.get<any[]>("/campaigns/")).data, refetchInterval: 20_000 });
  const [editing, setEditing] = useState<any | "new" | null>(null);
  const del = useMutation({ mutationFn: (id: number) => api.delete(`/campaigns/${id}/`), onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }), onError: (e) => toast.error(errMsg(e)) });
  if (me && !isManager) return <p className="text-stone-500">Campaigns are available to managers and admins.</p>;

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Email campaigns</h1>
        <button onClick={() => setEditing("new")} className="bg-brand text-white rounded-md px-3 py-1.5 text-sm">+ New campaign</button>
      </div>
      <p className="text-sm text-stone-500">One-off emails to a group of leads. Replies come back to the Inbox. Unsubscribed and bounced addresses are always skipped, and sending respects the daily limit and office hours.</p>
      <div className="bg-white border rounded-lg divide-y">
        {data?.map((c) => (
          <div key={c.id} className="p-3 flex flex-wrap items-center gap-3 text-sm">
            <Link href={c.status === "draft" ? "#" : `/campaigns/${c.id}`} onClick={(e) => { if (c.status === "draft") { e.preventDefault(); setEditing(c); } }} className="font-medium text-brand">{c.name}</Link>
            <span className={`text-xs rounded-full px-2 py-0.5 ${STATUS_STYLE[c.status]}`}>{c.status}</span>
            <span className="text-stone-500 truncate max-w-xs">{c.subject}</span>
            <span className="text-xs text-stone-400 ml-auto">{c.counts.recipients ? `${c.counts.sent}/${c.counts.recipients} sent` : "not sent"}{c.scheduled_at && c.status === "scheduled" ? ` · ${format(new Date(c.scheduled_at), "d MMM HH:mm")}` : ""}</span>
            {["draft", "cancelled"].includes(c.status) && <button onClick={() => confirm(`Delete "${c.name}"?`) && del.mutate(c.id)} className="text-xs text-red-600">Delete</button>}
          </div>
        ))}
        {data?.length === 0 && <p className="p-6 text-sm text-stone-400 text-center">No campaigns yet.</p>}
      </div>
      {editing && <CampaignEditor campaign={editing === "new" ? null : editing} onClose={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["campaigns"] }); }} onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["campaigns"] }); }} />}
    </div>
  );
}
