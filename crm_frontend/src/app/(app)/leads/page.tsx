"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import ScoreBadge from "@/components/ScoreBadge";
import ImportCsvModal from "@/components/ImportCsvModal";
import { api, Lead, Paged, STATUSES, statusLabel } from "@/lib/api";

function LeadsInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const isManager = me?.role === "admin" || me?.role === "manager";
  const params = Object.fromEntries(sp.entries());

  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(sp.toString());
    v ? next.set(k, v) : next.delete(k);
    next.delete("page");
    router.replace(`/leads?${next}`);
  };

  const { data, isLoading } = useQuery({
    queryKey: ["leads", params],
    queryFn: async () => (await api.get<Paged<Lead>>("/leads/", { params })).data,
  });

  const create = useMutation({
    mutationFn: (body: object) => api.post("/leads/", body),
    onSuccess: () => { toast.success("Lead created"); setShowNew(false); qc.invalidateQueries({ queryKey: ["leads"] }); },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? "Could not create lead"),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Leads {data && <span className="text-stone-400 text-base">({data.count})</span>}</h1>
        <div className="flex gap-2">
          <a href={`/api/crm/leads/export/?${new URLSearchParams(params)}`} className="border rounded-md px-3 py-1.5 text-sm bg-white whitespace-nowrap">Export CSV</a>
          {isManager && <button onClick={() => setShowImport(true)} className="border rounded-md px-3 py-1.5 text-sm bg-white whitespace-nowrap">Import CSV</button>}
          <button onClick={() => setShowNew(true)} className="bg-brand text-white rounded-md px-3 py-1.5 text-sm whitespace-nowrap">+ New lead</button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <input defaultValue={params.q ?? ""} placeholder="Search name / phone / email"
          onKeyDown={(e) => e.key === "Enter" && setParam("q", e.currentTarget.value)}
          className="border rounded-md px-3 py-1.5 text-sm bg-white w-full sm:w-64" />
        <select value={params.status ?? ""} onChange={(e) => setParam("status", e.target.value)} className="border rounded-md px-2 py-1.5 text-sm bg-white">
          <option value="">All statuses</option>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select value={params.assigned_to ?? ""} onChange={(e) => setParam("assigned_to", e.target.value)} className="border rounded-md px-2 py-1.5 text-sm bg-white">
          <option value="">Anyone</option><option value="me">Assigned to me</option><option value="unassigned">Unassigned</option>
        </select>
        <select value={params.needs_reply ?? ""} onChange={(e) => setParam("needs_reply", e.target.value)} className="border rounded-md px-2 py-1.5 text-sm bg-white">
          <option value="">Any email state</option><option value="true">↩ Needs reply</option>
        </select>
        <select value={params.band ?? ""} onChange={(e) => setParam("band", e.target.value)} className="border rounded-md px-2 py-1.5 text-sm bg-white">
          <option value="">Any score</option><option value="hot">🔥 Hot (60+)</option><option value="warm">Warm (30–59)</option><option value="cold">Cold (&lt;30)</option>
        </select>
        <select value={params.ordering ?? ""} onChange={(e) => setParam("ordering", e.target.value)} className="border rounded-md px-2 py-1.5 text-sm bg-white">
          <option value="">Newest first</option><option value="-score">Highest score</option><option value="next_followup_at">Next follow-up</option>
        </select>
        <select value={params.is_international ?? ""} onChange={(e) => setParam("is_international", e.target.value)} className="border rounded-md px-2 py-1.5 text-sm bg-white">
          <option value="">India + International</option><option value="false">India</option><option value="true">International</option>
        </select>
      </div>

      {/* phones: one card per lead instead of the 7-column table */}
      <div className="md:hidden space-y-2">
        {isLoading && <p className="text-sm text-stone-500">Loading…</p>}
        {data?.results.map((l) => (
          <Link key={l.id} href={`/leads/${l.id}`} className="block bg-white border border-stone-200 rounded-lg p-3 text-sm active:bg-stone-50">
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0">
                <div className="font-medium text-brand truncate">{l.name}</div>
                <div className="text-stone-500">{l.phone}</div>
              </div>
              <ScoreBadge score={l.score} band={l.score_band} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-stone-500">
              <span className="text-stone-700">{statusLabel(l.status)}</span>·<span>{l.source}</span>·
              <span>{l.assigned_to_name || <span className="text-amber-600">Unassigned</span>}</span>·<span>{format(new Date(l.created_at), "d MMM, HH:mm")}</span>
              {l.needs_reply && <span className="bg-red-100 text-red-700 rounded px-1.5">↩ Needs reply</span>}
              {l.email_invalid && <span className="bg-stone-200 text-stone-600 rounded px-1.5">bad email</span>}
              {l.is_international && <span className="bg-sky-100 text-sky-700 rounded px-1.5">{l.country || "Intl"}</span>}
            </div>
          </Link>
        ))}
        {data?.results.length === 0 && <p className="p-6 text-center text-stone-400">No leads</p>}
      </div>

      <div className="hidden md:block bg-white border border-stone-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr><th className="p-3">Name</th><th>Score</th><th>Phone</th><th>Status</th><th>Source</th><th>Assigned</th><th>Created</th></tr>
          </thead>
          <tbody>
            {isLoading && <tr><td className="p-3" colSpan={7}>Loading…</td></tr>}
            {data?.results.map((l) => (
              <tr key={l.id} className="border-t hover:bg-stone-50">
                <td className="p-3"><Link href={`/leads/${l.id}`} className="font-medium text-brand">{l.name}</Link>
                  {l.needs_reply && <span className="ml-2 text-xs bg-red-100 text-red-700 rounded px-1.5" title="This lead replied by email">↩ Needs reply</span>}
                  {l.email_invalid && <span className="ml-2 text-xs bg-stone-200 text-stone-600 rounded px-1.5" title="Email bounced">bad email</span>}
                  {l.is_international && <span className="ml-2 text-xs bg-sky-100 text-sky-700 rounded px-1.5">{l.country || "Intl"}</span>}</td>
                <td><ScoreBadge score={l.score} band={l.score_band} /></td>
                <td>{l.phone}</td><td>{statusLabel(l.status)}</td><td>{l.source}</td>
                <td>{l.assigned_to_name || <span className="text-amber-600">Unassigned</span>}</td>
                <td>{format(new Date(l.created_at), "d MMM, HH:mm")}</td>
              </tr>
            ))}
            {data?.results.length === 0 && <tr><td className="p-6 text-center text-stone-400" colSpan={7}>No leads</td></tr>}
          </tbody>
        </table>
      </div>

      {showImport && <ImportCsvModal onClose={() => setShowImport(false)} />}
      {showNew && (
        <div className="fixed inset-0 bg-black/40 grid place-items-center z-10" onClick={() => setShowNew(false)}>
          <form onClick={(e) => e.stopPropagation()} className="bg-white rounded-xl p-6 w-full max-w-md space-y-3"
            onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); create.mutate({ name: f.get("name"), phone: f.get("phone"), email: f.get("email"), country: f.get("country"), source: f.get("source") }); }}>
            <h2 className="font-semibold">New lead</h2>
            <input name="name" placeholder="Name" required className="w-full border rounded-md px-3 py-2" />
            <input name="phone" placeholder="Phone (+91…)" className="w-full border rounded-md px-3 py-2" />
            <input name="email" type="email" placeholder="Email" className="w-full border rounded-md px-3 py-2" />
            <input name="country" placeholder="Country" className="w-full border rounded-md px-3 py-2" />
            <select name="source" defaultValue="manual" className="w-full border rounded-md px-3 py-2">
              {["manual", "phone", "walk_in", "whatsapp", "referral", "partner"].map((s) => <option key={s}>{s}</option>)}
            </select>
            <button className="w-full bg-brand text-white rounded-md py-2">Create</button>
          </form>
        </div>
      )}
    </div>
  );
}

export default function LeadsPage() {
  return <Suspense fallback={null}><LeadsInner /></Suspense>;
}
