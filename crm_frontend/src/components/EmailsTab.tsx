"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { useState } from "react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";

type Msg = { id: number; direction: "in" | "out"; status: string; from_address: string; subject: string; body_text: string; body_full: string; error: string; sent_by_name: string | null; attachments: { filename: string }[]; created_at: string; opened_at: string | null; clicked_at: string | null };
type Thread = { id: number; subject: string; needs_reply: boolean; last_message_at: string; messages: Msg[] };
type Data = { threads: Thread[]; can_email: boolean; email: string; email_invalid: boolean; email_opt_out: boolean; reply_capture: boolean; sender: { formatted: string; used_fallback: boolean } };

const STATUS_STYLE: Record<string, string> = {
  delivered: "text-green-700", sent: "text-stone-500", queued: "text-stone-400", delayed: "text-amber-600",
  bounced: "text-red-600", failed: "text-red-600", complained: "text-red-600", received: "text-stone-500",
};
const input = "w-full border rounded-md px-3 py-2 bg-white text-sm";

export default function EmailsTab({ leadId, leadName }: { leadId: number; leadName: string }) {
  const qc = useQueryClient();
  const key = ["emails", String(leadId)];
  const { data } = useQuery({ queryKey: key, queryFn: async () => (await api.get<Data>(`/leads/${leadId}/emails/`)).data });
  const refresh = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["lead", String(leadId)] }); qc.invalidateQueries({ queryKey: ["inbox"] }); qc.invalidateQueries({ queryKey: ["inbox-count"] }); };
  const resolve = useMutation({ mutationFn: (id: number) => api.post(`/email-threads/${id}/resolve/`), onSuccess: refresh });
  if (!data) return <p className="text-stone-500 text-sm">Loading…</p>;

  return (
    <div className="space-y-4">
      {!data.email && <Banner tone="red">This lead has no email address. Add one in the Profile tab to send emails.</Banner>}
      {data.email_invalid && <Banner tone="red">Emails to <b>{data.email}</b> bounced earlier, so sending is blocked. Correct the address in the Profile tab.</Banner>}
      {data.email_opt_out && <Banner tone="amber">This lead unsubscribed or marked an email as spam. Automated and campaign emails are off; you can still write to them one-to-one.</Banner>}
      {data.email && !data.reply_capture && <Banner tone="amber">Replies from this lead will <b>not</b> appear here yet: the reply address isn't set up (Settings → Email).</Banner>}

      <Automation leadId={leadId} canEnrol={data.can_email && !data.email_opt_out} />

      {data.can_email && <Composer leadId={leadId} leadName={leadName} data={data} onSent={refresh} />}

      {data.threads.length === 0 && <p className="text-sm text-stone-400">No emails yet.</p>}
      {data.threads.map((t) => (
        <section key={t.id} className="bg-white border rounded-lg">
          <header className="flex items-center gap-3 px-4 py-2 border-b text-sm">
            <b className="truncate">{t.subject || "(no subject)"}</b>
            {t.needs_reply && <span className="rounded-full bg-red-100 text-red-700 text-xs px-2 py-0.5 font-medium">Needs reply</span>}
            <span className="ml-auto text-xs text-stone-400 shrink-0">{format(new Date(t.last_message_at), "d MMM HH:mm")}</span>
            {t.needs_reply && <button onClick={() => resolve.mutate(t.id)} className="text-xs border rounded px-2 py-1 bg-white shrink-0">Mark handled</button>}
          </header>
          <div className="p-4 space-y-3">
            {t.messages.map((m) => <Bubble key={m.id} m={m} />)}
            {data.can_email && <ReplyBox leadId={leadId} thread={t} onSent={refresh} />}
          </div>
        </section>
      ))}
    </div>
  );
}

function Banner({ tone, children }: { tone: "red" | "amber"; children: React.ReactNode }) {
  return <div className={`rounded-lg border px-3 py-2 text-sm ${tone === "red" ? "bg-red-50 border-red-200 text-red-800" : "bg-amber-50 border-amber-200 text-amber-800"}`}>{children}</div>;
}

function Bubble({ m }: { m: Msg }) {
  const [full, setFull] = useState(false);
  const mine = m.direction === "out";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${mine ? "bg-stone-100" : "bg-amber-50 border border-amber-200"}`}>
        <div className="text-xs text-stone-500 mb-1 flex flex-wrap gap-x-2">
          <span>{mine ? `You${m.sent_by_name ? ` (${m.sent_by_name})` : ""}` : m.from_address}</span>
          <span>{format(new Date(m.created_at), "d MMM HH:mm")}</span>
          <span className={STATUS_STYLE[m.status] ?? ""}>{m.status}</span>
          {m.clicked_at && <span className="text-green-700">clicked</span>}
        </div>
        <div className="whitespace-pre-wrap break-words">{full ? m.body_full || m.body_text : m.body_text}</div>
        {!mine && m.body_full && m.body_full !== m.body_text && (
          <button onClick={() => setFull(!full)} className="text-xs text-stone-400 mt-1">{full ? "Hide quoted text" : "Show quoted text"}</button>
        )}
        {m.attachments?.length > 0 && <div className="text-xs text-stone-500 mt-1">📎 {m.attachments.map((a) => a.filename).join(", ")} <span className="text-stone-400">(attachments aren't downloaded into the CRM yet)</span></div>}
        {m.error && <div className="text-xs text-red-600 mt-1">{m.error}</div>}
      </div>
    </div>
  );
}

function ReplyBox({ leadId, thread, onSent }: { leadId: number; thread: Thread; onSent: () => void }) {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState("");
  const send = useMutation({
    mutationFn: () => api.post(`/leads/${leadId}/emails/`, { thread_id: thread.id, subject: thread.subject, body }),
    onSuccess: () => { toast.success("Reply sent"); setBody(""); setOpen(false); onSent(); },
    onError: (e) => toast.error(errMsg(e)),
  });
  if (!open) return <button onClick={() => setOpen(true)} className="text-sm border rounded-md px-3 py-1.5 bg-white">↩ Reply</button>;
  return (
    <div className="space-y-2">
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} autoFocus placeholder="Write your reply…" className={input} />
      <div className="flex gap-2">
        <button disabled={!body.trim() || send.isPending} onClick={() => send.mutate()} className="bg-brand text-white rounded-md px-4 py-1.5 text-sm disabled:opacity-50">{send.isPending ? "Sending…" : "Send reply"}</button>
        <button onClick={() => setOpen(false)} className="text-sm text-stone-500">Cancel</button>
      </div>
    </div>
  );
}

function Composer({ leadId, leadName, data, onSent }: { leadId: number; leadName: string; data: Data; onSent: () => void }) {
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [copy, setCopy] = useState(false);
  const [preview, setPreview] = useState<{ subject: string; body: string; from: string } | null>(null);
  const { data: templates } = useQuery({ queryKey: ["email-templates"], queryFn: async () => (await api.get<any[]>("/email-templates/")).data });

  const send = useMutation({
    mutationFn: () => api.post(`/leads/${leadId}/emails/`, { subject, body, copy_to_sender: copy }),
    onSuccess: () => { toast.success(`Email sent to ${leadName}`); setSubject(""); setBody(""); setPreview(null); setOpen(false); onSent(); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const doPreview = useMutation({ mutationFn: async () => (await api.post(`/leads/${leadId}/emails/preview/`, { subject, body })).data, onSuccess: setPreview, onError: (e) => toast.error(errMsg(e)) });

  if (!open) return <button onClick={() => setOpen(true)} className="bg-brand text-white rounded-md px-4 py-2 text-sm">✉ New email</button>;
  return (
    <div className="bg-white border rounded-lg p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
        <span>From <b className="text-stone-700">{data.sender.formatted}</b></span><span>→ <b className="text-stone-700">{data.email}</b></span>
        {data.sender.used_fallback && <span className="text-amber-600">your own address isn't on a verified domain, so the default address is used</span>}
      </div>
      <select value="" onChange={(e) => { const t = templates?.find((x) => String(x.id) === e.target.value); if (t) { setSubject(t.subject); setBody(t.body); setPreview(null); } }} className="border rounded-md px-2 py-1.5 text-sm bg-white">
        <option value="">Use a template…</option>
        {templates?.filter((t) => t.is_active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
      <input value={subject} onChange={(e) => { setSubject(e.target.value); setPreview(null); }} placeholder="Subject" className={input} />
      <textarea value={body} onChange={(e) => { setBody(e.target.value); setPreview(null); }} rows={7} placeholder={"Hi {{first_name}},\n\n…"} className={input} />
      <p className="text-xs text-stone-400">Merge fields: {"{{first_name}} {{name}} {{treatment}} {{sender_name}} {{clinic_name}} {{clinic_phone}} {{clinic_website}}"}. Your signature is added automatically.</p>
      {preview && (
        <div className="border rounded-md bg-stone-50 p-3 text-sm space-y-1">
          <div className="text-xs text-stone-500">Preview · from {preview.from}</div>
          <div><b>{preview.subject}</b></div>
          <div className="whitespace-pre-wrap">{preview.body}</div>
        </div>
      )}
      <label className="text-xs flex items-center gap-2"><input type="checkbox" checked={copy} onChange={(e) => setCopy(e.target.checked)} /> Send me a copy (BCC)</label>
      <div className="flex gap-2">
        <button disabled={!subject.trim() || !body.trim() || send.isPending} onClick={() => send.mutate()} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-50">{send.isPending ? "Sending…" : "Send"}</button>
        <button disabled={!subject.trim() || !body.trim() || doPreview.isPending} onClick={() => doPreview.mutate()} className="border rounded-md px-4 py-2 text-sm bg-white disabled:opacity-50">Preview</button>
        <button onClick={() => setOpen(false)} className="text-sm text-stone-500 px-2">Cancel</button>
      </div>
    </div>
  );
}


const STOP_REASON: Record<string, string> = { replied: "they replied", progressed: "the lead moved on", booked: "an appointment was booked", unsubscribed: "they unsubscribed", bad_email: "the email bounced", no_email: "no email address", deleted: "the lead was archived", manual: "stopped by a person", send_failed: "sending kept failing" };

function Automation({ leadId, canEnrol }: { leadId: number; canEnrol: boolean }) {
  const qc = useQueryClient();
  const key = ["enrollments-lead", String(leadId)];
  const { data: rows } = useQuery({ queryKey: key, queryFn: async () => (await api.get<any[]>(`/leads/${leadId}/enrollments/`)).data });
  const { data: seqs } = useQuery({ queryKey: ["sequences"], queryFn: async () => (await api.get<any[]>("/sequences/")).data });
  const [pick, setPick] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["sequences"] }); };
  const stop = useMutation({ mutationFn: (id: number) => api.post(`/enrollments/${id}/stop/`), onSuccess: refresh, onError: (e) => toast.error(errMsg(e)) });
  const enrol = useMutation({ mutationFn: () => api.post(`/leads/${leadId}/enroll/`, { sequence_id: Number(pick) }), onSuccess: () => { toast.success("Enrolled"); setPick(""); refresh(); }, onError: (e) => toast.error(errMsg(e)) });
  const available = seqs?.filter((s) => s.is_active) ?? [];
  if (!rows?.length && !available.length) return null;
  return (
    <div className="bg-white border rounded-lg p-3 space-y-2 text-sm">
      <div className="font-medium">Automated emails</div>
      {rows?.map((r) => (
        <div key={r.id} className="flex flex-wrap items-center gap-3">
          <b>{r.sequence_name}</b>
          <span className={r.state === "active" ? "text-sky-700" : "text-stone-500"}>{r.state === "stopped" ? `stopped — ${STOP_REASON[r.stop_reason] ?? r.stop_reason}` : r.state}</span>
          <span className="text-xs text-stone-400">email {Math.min(r.current_step + (r.state === "active" ? 1 : 0), r.total_steps)} of {r.total_steps}</span>
          {r.state === "active" && r.next_send_at && <span className="text-xs text-stone-400">next: {format(new Date(r.next_send_at), "d MMM HH:mm")}</span>}
          {r.state === "active" && <button onClick={() => stop.mutate(r.id)} className="ml-auto text-xs text-red-600 border rounded px-2 py-0.5 bg-white">Stop</button>}
        </div>
      ))}
      {canEnrol && available.length > 0 && (
        <div className="flex gap-2 items-center pt-1">
          <select value={pick} onChange={(e) => setPick(e.target.value)} className="border rounded-md px-2 py-1.5 text-sm bg-white"><option value="">Enrol in a sequence…</option>{available.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
          <button disabled={!pick || enrol.isPending} onClick={() => enrol.mutate()} className="border rounded-md px-3 py-1.5 text-sm bg-white disabled:opacity-50">Enrol</button>
        </div>
      )}
    </div>
  );
}
