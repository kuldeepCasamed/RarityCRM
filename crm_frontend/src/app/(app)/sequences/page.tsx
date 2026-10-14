"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { api, errMsg, STATUSES } from "@/lib/api";

type Step = { id?: number; position: number; delay_hours: number; template: number; template_name?: string };
type Seq = {
  id: number; name: string; description: string; is_active: boolean; auto_enroll: boolean; auto_enroll_sources: string[]; run_while_status: string[];
  from_user: number | null; from_user_name: string | null; steps: Step[];
  stats: { active: number; completed: number; stopped: number; replied: number; booked: number; unsubscribed: number; emails_sent: number };
};
const SOURCES = ["website", "google_ads", "meta_ads", "whatsapp", "phone", "walk_in", "referral", "partner", "csv", "manual"];
const input = "w-full border rounded-md px-3 py-2 bg-white text-sm";
const STOP_REASON: Record<string, string> = { replied: "replied", progressed: "moved on", booked: "booked", unsubscribed: "unsubscribed", bad_email: "bad email", no_email: "no email", deleted: "lead archived", manual: "stopped by a person", send_failed: "sending failed" };

const delayLabel = (h: number, first: boolean) => (h === 0 ? (first ? "immediately" : "right after") : h % 24 === 0 ? `${h / 24} day${h / 24 > 1 ? "s" : ""} ${first ? "after enrolment" : "later"}` : `${h} hours ${first ? "after enrolment" : "later"}`);

export default function SequencesPage() {
  const qc = useQueryClient();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const isManager = me?.role === "admin" || me?.role === "manager";
  const { data: seqs } = useQuery({ queryKey: ["sequences"], queryFn: async () => (await api.get<Seq[]>("/sequences/")).data });
  const { data: st } = useQuery({ queryKey: ["email-status"], queryFn: async () => (await api.get<any>("/email/status/")).data });
  const [editing, setEditing] = useState<Seq | "new" | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["sequences"] });
  const toggle = useMutation({ mutationFn: (s: Seq) => api.patch(`/sequences/${s.id}/`, { is_active: !s.is_active }), onSuccess: refresh, onError: (e) => toast.error(errMsg(e)) });
  const del = useMutation({ mutationFn: (id: number) => api.delete(`/sequences/${id}/`), onSuccess: () => { toast.success("Deleted"); refresh(); }, onError: (e) => toast.error(errMsg(e)) });

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Email sequences</h1>
        {isManager && <button onClick={() => setEditing("new")} className="bg-brand text-white rounded-md px-3 py-1.5 text-sm">+ New sequence</button>}
      </div>
      <p className="text-sm text-stone-500">Automatic follow-up emails. A sequence stops by itself when the lead replies, books an appointment, moves on in the pipeline, unsubscribes, or their email bounces.</p>
      {st && (
        <div className="text-xs text-stone-500 flex flex-wrap gap-x-4">
          <span>Sent today: <b>{st.sent_today}</b> / {st.daily_cap} (daily limit)</span>
          <span>Follow-ups go out between {st.send_window}</span>
          {!st.resend_key && <span className="text-red-600">Email isn't configured, so nothing will be sent.</span>}
          {!st.reply_domain && <span className="text-amber-600">Reply capture isn't set up, so replies won't stop a sequence.</span>}
        </div>
      )}
      {seqs?.length === 0 && <p className="text-stone-400 text-sm py-6 text-center">No sequences yet.{isManager && " Create one (you need at least one email template first, in Settings → Email)."}</p>}
      {seqs?.map((s) => (
        <section key={s.id} className="bg-white border rounded-lg p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <b className="text-base">{s.name}</b>
            <span className={`text-xs rounded-full px-2 py-0.5 font-medium ${s.is_active ? "bg-green-100 text-green-700" : "bg-stone-200 text-stone-600"}`}>{s.is_active ? "Active" : "Paused"}</span>
            {s.auto_enroll && <span className="text-xs text-stone-500">auto-enrols new leads from {s.auto_enroll_sources.join(", ")}</span>}
            {isManager && (
              <span className="ml-auto flex gap-3 text-xs">
                <button onClick={() => toggle.mutate(s)} className="text-brand">{s.is_active ? "Pause" : "Resume"}</button>
                <button onClick={() => setEditing(s)} className="text-brand">Edit</button>
                <button onClick={() => confirm(`Delete "${s.name}"?`) && del.mutate(s.id)} className="text-red-600">Delete</button>
              </span>
            )}
          </div>
          {s.description && <p className="text-sm text-stone-500">{s.description}</p>}
          <ol className="text-sm space-y-1">
            {s.steps.map((st2, i) => <li key={st2.id ?? i}><span className="text-stone-400">{i + 1}.</span> <b>{st2.template_name}</b> <span className="text-stone-500">— {delayLabel(st2.delay_hours, i === 0)}</span></li>)}
          </ol>
          <div className="flex flex-wrap gap-2 text-xs">
            {[["Running", s.stats.active, "bg-sky-100 text-sky-700"], ["Completed", s.stats.completed, "bg-stone-100 text-stone-700"], ["Replied", s.stats.replied, "bg-green-100 text-green-700"],
              ["Booked", s.stats.booked, "bg-green-100 text-green-700"], ["Unsubscribed", s.stats.unsubscribed, "bg-amber-100 text-amber-700"], ["Emails sent", s.stats.emails_sent, "bg-stone-100 text-stone-700"]].map(([l, n, c]) => (
              <span key={l as string} className={`rounded-full px-2 py-0.5 ${c}`}>{l}: <b>{n as number}</b></span>
            ))}
            <button onClick={() => setOpen(open === s.id ? null : s.id)} className="ml-auto text-brand">{open === s.id ? "Hide leads" : "Show leads"}</button>
          </div>
          {open === s.id && <Enrollments seqId={s.id} onChange={refresh} />}
        </section>
      ))}
      {editing && <Editor seq={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />}
    </div>
  );
}

function Enrollments({ seqId, onChange }: { seqId: number; onChange: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["enrollments", seqId], queryFn: async () => (await api.get<any[]>(`/sequences/${seqId}/enrollments/`)).data });
  const stop = useMutation({ mutationFn: (id: number) => api.post(`/enrollments/${id}/stop/`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["enrollments", seqId] }); onChange(); }, onError: (e) => toast.error(errMsg(e)) });
  if (!data) return <p className="text-xs text-stone-400">Loading…</p>;
  if (!data.length) return <p className="text-xs text-stone-400">No leads in this sequence yet.</p>;
  return (
    <div className="border rounded-md divide-y max-h-72 overflow-y-auto text-sm">
      {data.map((e) => (
        <div key={e.id} className="px-3 py-1.5 flex items-center gap-3">
          <Link href={`/leads/${e.lead}?tab=Emails`} className="text-brand w-40 truncate">{e.lead_name}</Link>
          <span className={e.state === "active" ? "text-sky-700" : "text-stone-500"}>{e.state === "stopped" ? `stopped: ${STOP_REASON[e.stop_reason] ?? e.stop_reason}` : e.state}</span>
          <span className="text-xs text-stone-400">step {Math.min(e.current_step + 1, e.total_steps)}/{e.total_steps}</span>
          {e.state === "active" && e.next_send_at && <span className="text-xs text-stone-400">next {format(new Date(e.next_send_at), "d MMM HH:mm")}</span>}
          {e.state === "active" && <button onClick={() => stop.mutate(e.id)} className="ml-auto text-xs text-red-600">Stop</button>}
        </div>
      ))}
    </div>
  );
}

function Editor({ seq, onClose, onSaved }: { seq: Seq | null; onClose: () => void; onSaved: () => void }) {
  const { data: templates } = useQuery({ queryKey: ["email-templates"], queryFn: async () => (await api.get<any[]>("/email-templates/")).data });
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: async () => (await api.get<any[]>("/users/")).data });
  const [name, setName] = useState(seq?.name ?? "");
  const [description, setDescription] = useState(seq?.description ?? "");
  const [active, setActive] = useState(seq?.is_active ?? true);
  const [auto, setAuto] = useState(seq?.auto_enroll ?? false);
  const [sources, setSources] = useState<string[]>(seq?.auto_enroll_sources ?? ["website"]);
  const [statuses, setStatuses] = useState<string[]>(seq?.run_while_status?.length ? seq.run_while_status : ["new", "contacted"]);
  const [fromUser, setFromUser] = useState<string>(seq?.from_user ? String(seq.from_user) : "");
  const [steps, setSteps] = useState<{ delay: number; unit: "hours" | "days"; template: string }[]>(
    seq?.steps.map((s) => (s.delay_hours % 24 === 0 && s.delay_hours > 0 ? { delay: s.delay_hours / 24, unit: "days" as const, template: String(s.template) } : { delay: s.delay_hours, unit: "hours" as const, template: String(s.template) }))
      ?? [{ delay: 0, unit: "days", template: "" }],
  );
  const [error, setError] = useState("");
  const toggleIn = (arr: string[], v: string, set: (a: string[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const move = (i: number, d: number) => setSteps((c) => { const n = [...c]; const j = i + d; if (j < 0 || j >= n.length) return c; [n[i], n[j]] = [n[j], n[i]]; return n; });

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name, description, is_active: active, auto_enroll: auto, auto_enroll_sources: auto ? sources : [], run_while_status: statuses, from_user: fromUser ? Number(fromUser) : null,
        steps: steps.map((s) => ({ delay_hours: s.unit === "days" ? s.delay * 24 : s.delay, template: Number(s.template) })),
      };
      return seq ? api.patch(`/sequences/${seq.id}/`, body) : api.post("/sequences/", body);
    },
    onSuccess: () => { toast.success("Sequence saved"); onSaved(); },
    onError: (e: any) => setError(errMsg(e)),
  });
  const valid = name.trim() && steps.length > 0 && steps.every((s) => s.template) && statuses.length > 0;
  let cumulative = 0;

  return (
    <div className="fixed inset-0 bg-black/40 z-20 overflow-y-auto p-4" onClick={onClose}>
      <div className="bg-white rounded-xl p-5 w-full max-w-2xl mx-auto space-y-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center"><h2 className="font-semibold">{seq ? `Edit "${seq.name}"` : "New sequence"}</h2><button onClick={onClose} className="text-stone-400">✕</button></div>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Welcome)" className={input} />
        <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description (optional)" className={input} />

        <div className="space-y-2">
          <div className="text-sm font-medium">Emails</div>
          {steps.map((s, i) => {
            cumulative += (s.unit === "days" ? s.delay * 24 : s.delay);
            return (
              <div key={i} className="flex flex-wrap items-center gap-2 text-sm bg-stone-50 border rounded-md p-2">
                <span className="text-stone-400 w-5">{i + 1}.</span>
                <span className="text-stone-500">{i === 0 ? "Send" : "Wait"}</span>
                <input type="number" min={0} value={s.delay} onChange={(e) => setSteps((c) => c.map((x, n) => (n === i ? { ...x, delay: Math.max(0, Number(e.target.value)) } : x)))} className="w-20 border rounded px-2 py-1" />
                <select value={s.unit} onChange={(e) => setSteps((c) => c.map((x, n) => (n === i ? { ...x, unit: e.target.value as any } : x)))} className="border rounded px-1 py-1 bg-white"><option value="days">days</option><option value="hours">hours</option></select>
                <span className="text-stone-400 text-xs">{i === 0 ? "after enrolment" : "after the previous"}</span>
                <select value={s.template} onChange={(e) => setSteps((c) => c.map((x, n) => (n === i ? { ...x, template: e.target.value } : x)))} className="border rounded px-2 py-1 bg-white flex-1 min-w-40">
                  <option value="">Choose a template…</option>{templates?.filter((t) => t.is_active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <span className="text-xs text-stone-400">(day {Math.round((cumulative / 24) * 10) / 10})</span>
                <button onClick={() => move(i, -1)} className="text-stone-400">↑</button><button onClick={() => move(i, 1)} className="text-stone-400">↓</button>
                <button onClick={() => setSteps((c) => (c.length > 1 ? c.filter((_, n) => n !== i) : c))} className="text-red-500">✕</button>
              </div>
            );
          })}
          <button onClick={() => setSteps((c) => [...c, { delay: 2, unit: "days", template: "" }])} className="text-sm border rounded-md px-3 py-1.5 bg-white">+ Add email</button>
          {templates?.length === 0 && <p className="text-xs text-amber-600">You have no templates yet. Create them in Settings → Email first.</p>}
        </div>

        <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active (sends emails)</label>
        <div className="space-y-1">
          <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Automatically enrol new leads that come from:</label>
          {auto && <div className="flex flex-wrap gap-2 pl-6">{SOURCES.map((s) => <label key={s} className="text-xs flex items-center gap-1"><input type="checkbox" checked={sources.includes(s)} onChange={() => toggleIn(sources, s, setSources)} />{s}</label>)}</div>}
        </div>
        <div className="space-y-1">
          <div className="text-sm">Keeps running only while the lead's status is:</div>
          <div className="flex flex-wrap gap-2">{STATUSES.map(([v, l]) => <label key={v} className="text-xs flex items-center gap-1"><input type="checkbox" checked={statuses.includes(v)} onChange={() => toggleIn(statuses, v, setStatuses)} />{l}</label>)}</div>
          <p className="text-xs text-stone-400">The default (New, Contacted) stops the moment a lead books or progresses. Pick only "Lost" for a win-back sequence.</p>
        </div>
        <label className="text-sm block">Sender when the lead has no assigned rep
          <select value={fromUser} onChange={(e) => setFromUser(e.target.value)} className={input + " mt-1"}><option value="">Default clinic address</option>{users?.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
        <p className="text-xs text-stone-400">Every email includes an unsubscribe link, and leads who unsubscribe or whose address bounces are never emailed again. Emails are sent from the assigned rep's sender identity.</p>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2 justify-end"><button onClick={onClose} className="border rounded-md px-4 py-2 text-sm">Cancel</button>
          <button disabled={!valid || save.isPending} onClick={() => { setError(""); save.mutate(); }} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-50">{save.isPending ? "Saving…" : "Save sequence"}</button></div>
      </div>
    </div>
  );
}
