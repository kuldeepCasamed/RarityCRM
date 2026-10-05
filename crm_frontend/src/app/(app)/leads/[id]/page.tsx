"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { use, useState } from "react";
import { toast } from "sonner";
import ScoreBadge from "@/components/ScoreBadge";
import QuotesTab from "@/components/QuotesTab";
import CallPanel from "@/components/CallPanel";
import LeadProfileForm from "@/components/LeadProfileForm";
import { api, dualTime, errMsg, Lead, leadTimezone, STATUSES } from "@/lib/api";

const TABS = ["Overview", "Profile", "Contacts", "Notes", "Tasks", "Appointments", "Quotes", "Calls", "History"] as const;

export default function LeadDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const key = ["lead", id];
  const { data: lead } = useQuery({ queryKey: key, queryFn: async () => (await api.get<Lead>(`/leads/${id}/`)).data });

  const pipeline = useMutation({
    mutationFn: (body: object) => api.patch(`/leads/${id}/pipeline/`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["history", id] }); toast.success("Updated"); },
  });
  const profile = useMutation({
    mutationFn: (body: object) => api.patch(`/leads/${id}/`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: key }); toast.success("Saved"); },
  });

  if (!lead) return <p className="text-stone-500">Loading…</p>;

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-3">{lead.name} <ScoreBadge score={lead.score} band={lead.score_band} /></h1>
          <p className="text-sm text-stone-500">{lead.phone} · {lead.email} · {lead.country}</p>
        </div>
        <div className="flex items-center gap-3">
          <CallPanel leadId={lead.id} leadName={lead.name} phone={lead.phone} />
          <select value={lead.status} onChange={(e) => pipeline.mutate({ status: e.target.value })} className="border rounded-md px-3 py-2 bg-white">
            {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
      </div>
      <Duplicates id={id} />
      <div className="flex gap-1 border-b">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-brand text-brand font-medium" : "text-stone-500"}`}>{t}</button>
        ))}
      </div>
      {tab === "Overview" && <ScoreCard lead={lead} />}
      {tab === "Overview" && <Overview lead={lead} onSave={(b) => profile.mutate(b)} onPipeline={(b) => pipeline.mutate(b)} />}
      {tab === "Profile" && <LeadProfileForm key={lead.updated_at} lead={lead} />}
      {tab === "Contacts" && <Contacts id={id} />}
      {tab === "Notes" && <Notes id={id} />}
      {tab === "Tasks" && <Tasks id={id} />}
      {tab === "Appointments" && <Appointments id={id} />}
      {tab === "Quotes" && <QuotesTab leadId={lead.id} leadEmail={lead.email} leadName={lead.name} />}
      {tab === "Calls" && <LeadCalls id={id} />}
      {tab === "History" && <History id={id} />}
    </div>
  );
}

function Overview({ lead, onSave, onPipeline }: { lead: Lead; onSave: (b: object) => void; onPipeline: (b: object) => void }) {
  return (
    <form className="bg-white border rounded-lg p-4 grid md:grid-cols-2 gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        onSave({ chief_complaint: f.get("chief_complaint"), budget_band: f.get("budget_band"), insurance: f.get("insurance"), urgency: f.get("urgency") });
        const fu = f.get("next_followup_at") as string;
        onPipeline({ priority: f.get("priority"), next_followup_at: fu ? new Date(fu).toISOString() : null });
      }}>
      <label className="text-sm md:col-span-2">Chief complaint / message<textarea name="chief_complaint" defaultValue={lead.chief_complaint} className="w-full border rounded-md p-2 mt-1" rows={3} /></label>
      <label className="text-sm">Budget<input name="budget_band" defaultValue={lead.budget_band} className="w-full border rounded-md p-2 mt-1" /></label>
      <label className="text-sm">Insurance<input name="insurance" defaultValue={lead.insurance} className="w-full border rounded-md p-2 mt-1" /></label>
      <label className="text-sm">Urgency<input name="urgency" defaultValue={lead.urgency} className="w-full border rounded-md p-2 mt-1" /></label>
      <label className="text-sm">Priority<select name="priority" defaultValue={lead.priority} className="w-full border rounded-md p-2 mt-1"><option>low</option><option>medium</option><option>high</option></select></label>
      <label className="text-sm">Next follow-up<input name="next_followup_at" type="datetime-local" defaultValue={lead.next_followup_at ? format(new Date(lead.next_followup_at), "yyyy-MM-dd'T'HH:mm") : ""} className="w-full border rounded-md p-2 mt-1" /></label>
      {lead.next_followup_at && (() => { const t = dualTime(lead.next_followup_at, leadTimezone(lead)); return <div className="text-xs text-stone-500 md:col-span-2">Follow-up: <b>{t.rep}</b> your time{t.lead && <> · <b>{t.lead}</b> lead's time ({leadTimezone(lead)})</>}</div>; })()}
      <div className="text-xs text-stone-500 md:col-span-2">Source: {lead.source} · Landing: {lead.landing_url || "—"} · Assigned: {lead.assigned_to_name || "Unassigned"}</div>
      <button className="bg-brand text-white rounded-md py-2 md:col-span-2">Save</button>
    </form>
  );
}

function useList<T>(name: string, id: string, path: string) {
  return useQuery({ queryKey: [name, id], queryFn: async () => (await api.get<T[]>(`/leads/${id}/${path}/`)).data });
}

function Contacts({ id }: { id: string }) {
  const qc = useQueryClient();
  const { data } = useList<any>("contacts", id, "contacts");
  const add = useMutation({
    mutationFn: (b: object) => api.post(`/leads/${id}/contacts/`, b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["contacts", id] }); qc.invalidateQueries({ queryKey: ["lead", id] }); },
  });
  return (
    <div className="space-y-3">
      <form className="bg-white border rounded-lg p-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); add.mutate(Object.fromEntries(f)); e.currentTarget.reset(); }}>
        <select name="method" className="border rounded-md p-2 text-sm">{["call", "whatsapp", "sms", "email", "visit"].map((m) => <option key={m}>{m}</option>)}</select>
        <select name="direction" className="border rounded-md p-2 text-sm"><option>outbound</option><option>inbound</option></select>
        <select name="outcome" className="border rounded-md p-2 text-sm"><option value="">outcome…</option>{["connected", "no_answer", "busy", "voicemail", "wrong_number", "callback_requested", "interested", "not_interested"].map((m) => <option key={m}>{m}</option>)}</select>
        <input name="notes" placeholder="Notes" className="border rounded-md p-2 text-sm flex-1 min-w-40" />
        <button className="bg-brand text-white rounded-md px-3 text-sm">Log contact</button>
      </form>
      {data?.map((c) => (
        <div key={c.id} className="bg-white border rounded-lg p-3 text-sm">
          <b>{c.method}</b> · {c.direction} · {c.outcome || "—"} <span className="text-stone-400">— {c.logged_by_name} · {format(new Date(c.contacted_at), "d MMM HH:mm")}</span>
          {c.notes && <p className="text-stone-600">{c.notes}</p>}
          {c.has_recording && <audio controls preload="none" src={`/api/crm/calls/${c.call_session}/recording/`} className="h-8 mt-1" />}
        </div>
      ))}
    </div>
  );
}

function Notes({ id }: { id: string }) {
  const qc = useQueryClient();
  const { data } = useList<any>("notes", id, "notes");
  const add = useMutation({ mutationFn: (body: string) => api.post(`/leads/${id}/notes/`, { body }), onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", id] }) });
  const del = useMutation({ mutationFn: (nid: number) => api.delete(`/notes/${nid}/`), onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", id] }), onError: () => toast.error("Not allowed") });
  return (
    <div className="space-y-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); add.mutate(f.get("body") as string); e.currentTarget.reset(); }}>
        <input name="body" required placeholder="Add an internal note…" className="flex-1 border rounded-md p-2 bg-white" />
        <button className="bg-brand text-white rounded-md px-3">Add</button>
      </form>
      {data?.map((n) => (
        <div key={n.id} className="bg-white border rounded-lg p-3 text-sm flex justify-between">
          <div>{n.body}<div className="text-xs text-stone-400">{n.author_name} · {format(new Date(n.created_at), "d MMM HH:mm")}</div></div>
          <button onClick={() => del.mutate(n.id)} className="text-xs text-red-600">Delete</button>
        </div>
      ))}
    </div>
  );
}

function Tasks({ id }: { id: string }) {
  const qc = useQueryClient();
  const { data } = useList<any>("ltasks", id, "tasks");
  const inv = () => { qc.invalidateQueries({ queryKey: ["ltasks", id] }); qc.invalidateQueries({ queryKey: ["mytasks"] }); };
  const add = useMutation({ mutationFn: (b: object) => api.post(`/leads/${id}/tasks/`, b), onSuccess: inv });
  const toggle = useMutation({ mutationFn: (t: any) => api.patch(`/tasks/${t.id}/`, { is_done: !t.is_done }), onSuccess: inv });
  return (
    <div className="space-y-3">
      <form className="flex gap-2 flex-wrap" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); add.mutate({ title: f.get("title"), due_at: new Date(f.get("due_at") as string).toISOString() }); e.currentTarget.reset(); }}>
        <input name="title" required placeholder="Follow-up task…" className="flex-1 border rounded-md p-2 bg-white min-w-40" />
        <input name="due_at" type="datetime-local" required className="border rounded-md p-2 bg-white" />
        <button className="bg-brand text-white rounded-md px-3">Add</button>
      </form>
      {data?.map((t) => (
        <label key={t.id} className="bg-white border rounded-lg p-3 text-sm flex items-center gap-2">
          <input type="checkbox" checked={t.is_done} onChange={() => toggle.mutate(t)} />
          <span className={t.is_done ? "line-through text-stone-400" : ""}>{t.title}</span>
          <span className="ml-auto text-xs text-stone-400">{format(new Date(t.due_at), "d MMM HH:mm")}</span>
        </label>
      ))}
    </div>
  );
}

function Appointments({ id }: { id: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["appts", id], queryFn: async () => (await api.get<any[]>("/appointments/", { params: { lead: id } })).data });
  const { data: doctors } = useQuery({ queryKey: ["doctors"], queryFn: async () => (await api.get<any[]>("/doctors/")).data });
  const add = useMutation({
    mutationFn: (b: object) => api.post("/appointments/", b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["appts", id] }); qc.invalidateQueries({ queryKey: ["lead", id] }); toast.success("Appointment booked"); },
    onError: () => toast.error("Could not book"),
  });
  return (
    <div className="space-y-3">
      <form className="flex gap-2 flex-wrap" onSubmit={(e) => {
        e.preventDefault(); const f = new FormData(e.currentTarget);
        const start = new Date(f.get("start") as string);
        add.mutate({ lead: Number(id), doctor: f.get("doctor") || null, kind: f.get("kind"), start_at: start.toISOString(), end_at: new Date(start.getTime() + 30 * 60000).toISOString() });
      }}>
        <input name="start" type="datetime-local" required className="border rounded-md p-2 bg-white" />
        <select name="doctor" className="border rounded-md p-2 bg-white"><option value="">Any doctor</option>{doctors?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
        <select name="kind" className="border rounded-md p-2 bg-white"><option value="consult">Consult</option><option value="treatment">Treatment</option><option value="follow_up">Follow-up</option></select>
        <button className="bg-brand text-white rounded-md px-3">Book (30 min)</button>
      </form>
      {data?.map((a) => (
        <div key={a.id} className="bg-white border rounded-lg p-3 text-sm">{format(new Date(a.start_at), "EEE d MMM, HH:mm")} · {a.kind} · <b>{a.status}</b></div>
      ))}
    </div>
  );
}

function History({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ["history", id], queryFn: async () => (await api.get<any[]>(`/leads/${id}/status-history/`)).data });
  return (
    <div className="space-y-2">
      {data?.map((h) => (
        <div key={h.id} className="bg-white border rounded-lg p-3 text-sm">
          {h.from_status || "—"} → <b>{h.to_status}</b> <span className="text-stone-400">{format(new Date(h.changed_at), "d MMM HH:mm")}</span>
        </div>
      ))}
    </div>
  );
}


function Duplicates({ id }: { id: string }) {
  const qc = useQueryClient();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const { data } = useQuery({ queryKey: ["dups", id], queryFn: async () => (await api.get<any[]>(`/leads/${id}/duplicates/`)).data });
  const merge = useMutation({
    mutationFn: (dupId: number) => api.post(`/leads/${id}/merge/`, { duplicate_id: dupId }),
    onSuccess: () => { toast.success("Merged"); ["lead", "dups", "contacts", "notes", "ltasks", "history"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const isManager = me?.role === "admin" || me?.role === "manager";
  if (!data?.length) return null;
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm space-y-2">
      <b>Possible duplicate{data.length > 1 ? "s" : ""}</b>
      {data.map((d) => (
        <div key={d.id} className="flex items-center gap-3">
          <a href={`/leads/${d.id}`} className="text-brand">{d.name}</a><span className="text-stone-500">{d.phone} {d.email}</span>
          {isManager && <button onClick={() => confirm(`Merge "${d.name}" into this lead? The duplicate will be archived.`) && merge.mutate(d.id)} className="ml-auto border rounded px-2 py-1 bg-white">Merge into this lead</button>}
        </div>
      ))}
    </div>
  );
}

function LeadCalls({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ["calls", id], queryFn: async () => (await api.get<any>("/calls/", { params: { lead: id } })).data.results });
  return (
    <div className="space-y-2">
      {data?.map((c: any) => (
        <div key={c.id} className="bg-white border rounded-lg p-3 text-sm flex items-center gap-3">
          <span>{format(new Date(c.created_at), "d MMM HH:mm")}</span><span className="text-stone-500">{c.user_name}</span>
          <b>{c.status}</b><span>{c.duration_sec ? `${Math.floor(c.duration_sec / 60)}:${String(c.duration_sec % 60).padStart(2, "0")}` : ""}</span>
          {c.has_recording && <audio controls preload="none" src={`/api/crm/calls/${c.id}/recording/`} className="h-8 ml-auto" />}
        </div>
      ))}
      {data?.length === 0 && <p className="text-stone-400 text-sm">No calls yet.</p>}
    </div>
  );
}


function ScoreCard({ lead }: { lead: Lead }) {
  const items = lead.score_breakdown ?? [];
  return (
    <div className="bg-white border rounded-lg p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">Lead score</h3>
        <ScoreBadge score={lead.score} band={lead.score_band} />
      </div>
      <div className="h-2 bg-stone-100 rounded-full my-3 overflow-hidden">
        <div className={`h-full ${lead.score_band === "hot" ? "bg-red-500" : lead.score_band === "warm" ? "bg-amber-500" : "bg-sky-500"}`} style={{ width: `${lead.score ?? 0}%` }} />
      </div>
      <ul className="text-sm divide-y">
        {items.map((b) => (
          <li key={b.label} className="flex justify-between py-1"><span>{b.label}</span>
            <span className={b.points < 0 ? "text-red-600" : "text-green-700"}>{b.points > 0 ? "+" : ""}{b.points}</span></li>
        ))}
        {items.length === 0 && <li className="py-1 text-stone-400">Not scored yet</li>}
      </ul>
      <p className="text-xs text-stone-400 mt-2">Updates automatically when contacts, appointments, stage or profile change, and nightly for inactivity.</p>
    </div>
  );
}
