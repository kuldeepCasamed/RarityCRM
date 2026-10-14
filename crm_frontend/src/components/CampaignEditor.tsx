"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, errMsg, STATUSES, TREATMENTS } from "@/lib/api";

const SOURCES = ["website", "google_ads", "meta_ads", "whatsapp", "phone", "walk_in", "referral", "partner", "csv", "manual"];
const input = "w-full border rounded-md px-3 py-2 bg-white text-sm";
type F = { status: string[]; source: string[]; band: string; is_international: string; country: string; tag: string; treatment: string; created_from: string; created_to: string; score_min: string };
const EMPTY: F = { status: [], source: [], band: "", is_international: "", country: "", tag: "", treatment: "", created_from: "", created_to: "", score_min: "" };

const fromApi = (f: any): F => ({ ...EMPTY, ...f, is_international: f?.is_international === undefined ? "" : String(f.is_international), score_min: f?.score_min === undefined ? "" : String(f.score_min) });
const toApi = (f: F) => {
  const o: any = {};
  (Object.keys(f) as (keyof F)[]).forEach((k) => {
    const v = f[k];
    if (Array.isArray(v) ? v.length : v !== "") o[k] = k === "is_international" ? v === "true" : v;
  });
  return o;
};

export default function CampaignEditor({ campaign, onClose, onSaved }: { campaign: any | null; onClose: () => void; onSaved: (c: any) => void }) {
  const { data: templates } = useQuery({ queryKey: ["email-templates"], queryFn: async () => (await api.get<any[]>("/email-templates/")).data });
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const [saved, setSaved] = useState<any | null>(campaign);
  const [name, setName] = useState(campaign?.name ?? "");
  const [subject, setSubject] = useState(campaign?.subject ?? "");
  const [body, setBody] = useState(campaign?.body ?? "");
  const [f, setF] = useState<F>(fromApi(campaign?.audience_filters));
  const [recent, setRecent] = useState<number>(campaign?.skip_recent_days ?? 3);
  const [openConv, setOpenConv] = useState<boolean>(campaign?.skip_open_conversations ?? true);
  const [when, setWhen] = useState<"asap" | "later">("asap");
  const [at, setAt] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  const [testTo, setTestTo] = useState("");

  // live audience preview (debounced)
  const [debounced, setDebounced] = useState({ f, recent, openConv });
  useEffect(() => { const t = setTimeout(() => setDebounced({ f, recent, openConv }), 500); return () => clearTimeout(t); }, [f, recent, openConv]);
  const { data: aud, isFetching } = useQuery({
    queryKey: ["audience", JSON.stringify(debounced)],
    queryFn: async () => (await api.post("/campaigns/audience/", { filters: toApi(debounced.f), skip_recent_days: debounced.recent, skip_open_conversations: debounced.openConv })).data,
  });

  const toggle = (k: "status" | "source", v: string) => setF((c) => ({ ...c, [k]: c[k].includes(v) ? c[k].filter((x) => x !== v) : [...c[k], v] }));
  const payload = () => ({ name, subject, body, audience_filters: toApi(f), skip_recent_days: recent, skip_open_conversations: openConv });
  const save = useMutation({
    mutationFn: async () => (saved ? (await api.patch(`/campaigns/${saved.id}/`, payload())).data : (await api.post("/campaigns/", payload())).data),
    onSuccess: (d) => { setSaved(d); setError(""); },
    onError: (e: any) => setError(errMsg(e)),
  });
  const sendTest = useMutation({
    mutationFn: async () => { const c = saved && JSON.stringify(payload()) === JSON.stringify({ name: saved.name, subject: saved.subject, body: saved.body, audience_filters: saved.audience_filters, skip_recent_days: saved.skip_recent_days, skip_open_conversations: saved.skip_open_conversations }) ? saved : await save.mutateAsync(); return (await api.post(`/campaigns/${c.id}/test/`, { to: testTo || undefined })).data; },
    onSuccess: (d) => toast.success(`Test sent to ${d.sent_to}`),
    onError: (e: any) => toast.error(errMsg(e)),
  });
  const launch = useMutation({
    mutationFn: async () => { const c = await save.mutateAsync(); return (await api.post(`/campaigns/${c.id}/send/`, when === "later" && at ? { scheduled_at: new Date(at).toISOString() } : {})).data; },
    onSuccess: (d) => { toast.success(`Campaign scheduled for ${d.recipients_frozen} people`); onSaved(d); },
    onError: (e: any) => { setError(errMsg(e)); setConfirm(false); },
  });
  const valid = name.trim() && subject.trim() && body.trim() && (aud?.eligible ?? 0) > 0 && !aud?.over_limit;
  const excl = aud ? [["no email", aud.no_email], ["bounced", aud.invalid], ["unsubscribed", aud.opted_out], ["emailed recently", aud.recently_emailed], ["waiting for a reply", aud.open_conversation]].filter(([, n]) => (n as number) > 0) : [];

  return (
    <div className="fixed inset-0 bg-black/40 z-20 overflow-y-auto p-4" onClick={onClose}>
      <div className="bg-white rounded-xl p-5 w-full max-w-3xl mx-auto space-y-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center"><h2 className="font-semibold">{campaign ? "Edit campaign" : "New campaign"}</h2><button onClick={onClose} className="text-stone-400">✕</button></div>

        <section className="space-y-2">
          <h3 className="text-sm font-medium">1. Message</h3>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Campaign name (internal)" className={input} />
          <select value="" onChange={(e) => { const t = templates?.find((x) => String(x.id) === e.target.value); if (t) { setSubject(t.subject); setBody(t.body); } }} className="border rounded-md px-2 py-1.5 text-sm bg-white">
            <option value="">Start from a template…</option>{templates?.filter((t) => t.is_active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" className={input} />
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={7} placeholder={"Hi {{first_name}},\n\n…"} className={input} />
          <p className="text-xs text-stone-400">Merge fields: {"{{first_name}} {{name}} {{treatment}} {{sender_name}} {{clinic_name}} {{clinic_phone}}"}. Sent from <b>{me?.name}</b> ({me?.email_from || me?.email}). An unsubscribe link and your clinic address are added automatically.</p>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-medium">2. Who should get it?</h3>
          <div className="grid md:grid-cols-2 gap-3 text-sm">
            <div><div className="text-xs text-stone-500 mb-1">Lead status</div><div className="flex flex-wrap gap-x-3 gap-y-1">{STATUSES.map(([v, l]) => <label key={v} className="text-xs flex items-center gap-1"><input type="checkbox" checked={f.status.includes(v)} onChange={() => toggle("status", v)} />{l}</label>)}</div></div>
            <div><div className="text-xs text-stone-500 mb-1">Source</div><div className="flex flex-wrap gap-x-3 gap-y-1">{SOURCES.map((v) => <label key={v} className="text-xs flex items-center gap-1"><input type="checkbox" checked={f.source.includes(v)} onChange={() => toggle("source", v)} />{v}</label>)}</div></div>
            <label className="text-xs text-stone-500">Treatment interest<select value={f.treatment} onChange={(e) => setF({ ...f, treatment: e.target.value })} className={input + " mt-1"}><option value="">Any</option>{TREATMENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="text-xs text-stone-500">Lead score<select value={f.band} onChange={(e) => setF({ ...f, band: e.target.value })} className={input + " mt-1"}><option value="">Any</option><option value="hot">Hot (60+)</option><option value="warm">Warm (30–59)</option><option value="cold">Cold (&lt;30)</option></select></label>
            <label className="text-xs text-stone-500">India / international<select value={f.is_international} onChange={(e) => setF({ ...f, is_international: e.target.value })} className={input + " mt-1"}><option value="">Both</option><option value="false">India only</option><option value="true">International only</option></select></label>
            <label className="text-xs text-stone-500">Country<input value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })} className={input + " mt-1"} placeholder="e.g. United Kingdom" /></label>
            <label className="text-xs text-stone-500">Created from<input type="date" value={f.created_from} onChange={(e) => setF({ ...f, created_from: e.target.value })} className={input + " mt-1"} /></label>
            <label className="text-xs text-stone-500">Created until<input type="date" value={f.created_to} onChange={(e) => setF({ ...f, created_to: e.target.value })} className={input + " mt-1"} /></label>
            <label className="text-xs text-stone-500">Tag<input value={f.tag} onChange={(e) => setF({ ...f, tag: e.target.value })} className={input + " mt-1"} placeholder="e.g. smile-studio" /></label>
            <label className="text-xs text-stone-500">Minimum score<input type="number" min={0} max={100} value={f.score_min} onChange={(e) => setF({ ...f, score_min: e.target.value })} className={input + " mt-1"} /></label>
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-stone-600">
            <label className="flex items-center gap-2">Skip anyone emailed by a sequence/campaign in the last <input type="number" min={0} max={90} value={recent} onChange={(e) => setRecent(Number(e.target.value))} className="w-14 border rounded px-1 py-0.5" /> days</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={openConv} onChange={(e) => setOpenConv(e.target.checked)} /> Skip leads who replied and are waiting for a person</label>
          </div>
          <div className={`rounded-lg border p-3 text-sm ${aud?.eligible ? "bg-green-50 border-green-200" : "bg-amber-50 border-amber-200"}`}>
            {!aud ? "Counting…" : (
              <>
                <div><b className="text-lg">{aud.eligible}</b> {aud.eligible === 1 ? "person" : "people"} will receive this {isFetching && <span className="text-stone-400 text-xs">updating…</span>}</div>
                <div className="text-xs text-stone-600">{aud.matching} leads match your filters{excl.length ? ` · left out: ${excl.map(([l, n]) => `${n} ${l}`).join(", ")}` : ""}</div>
                {aud.sample.length > 0 && <div className="text-xs text-stone-500 mt-1">e.g. {aud.sample.map((s: any) => s.name).join(", ")}{aud.eligible > aud.sample.length ? "…" : ""}</div>}
                {aud.over_limit && <div className="text-xs text-red-600 mt-1">That's more than the {aud.limit} allowed per campaign. Narrow the filters.</div>}
              </>
            )}
          </div>
          <p className="text-xs text-stone-400">People who unsubscribed or whose address bounced are never emailed, and can't be added back here.</p>
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-medium">3. Test and send</h3>
          <div className="flex flex-wrap gap-2 items-center">
            <input value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder={`Test address (default: ${me?.email ?? "you"})`} className="border rounded-md px-3 py-1.5 text-sm bg-white w-72" />
            <button disabled={!subject.trim() || !body.trim() || sendTest.isPending} onClick={() => sendTest.mutate()} className="border rounded-md px-3 py-1.5 text-sm bg-white disabled:opacity-50">{sendTest.isPending ? "Sending…" : "Send me a test"}</button>
            <button disabled={!name.trim() || !subject.trim() || !body.trim() || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: () => toast.success("Draft saved") })} className="border rounded-md px-3 py-1.5 text-sm bg-white disabled:opacity-50">Save draft</button>
          </div>
          {!confirm ? (
            <button disabled={!valid} onClick={() => setConfirm(true)} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-50">Review &amp; send…</button>
          ) : (
            <div className="border rounded-lg p-3 space-y-2 bg-stone-50 text-sm">
              <div>Send <b>"{subject}"</b> to <b>{aud?.eligible}</b> people?</div>
              <label className="flex items-center gap-2"><input type="radio" checked={when === "asap"} onChange={() => setWhen("asap")} /> As soon as office hours and the daily limit allow</label>
              <label className="flex items-center gap-2"><input type="radio" checked={when === "later"} onChange={() => setWhen("later")} /> Schedule for <input type="datetime-local" value={at} onChange={(e) => { setAt(e.target.value); setWhen("later"); }} className="border rounded px-2 py-1 bg-white" /></label>
              <p className="text-xs text-stone-500">The audience is locked when you confirm. Anyone who unsubscribes before their email goes out is skipped. You can pause or cancel any time.</p>
              <div className="flex gap-2"><button disabled={launch.isPending || (when === "later" && !at)} onClick={() => launch.mutate()} className="bg-brand text-white rounded-md px-4 py-2 disabled:opacity-50">{launch.isPending ? "Scheduling…" : "Yes, send"}</button>
                <button onClick={() => setConfirm(false)} className="text-stone-500 px-2">Back</button></div>
            </div>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}
          {saved && <p className="text-xs text-green-700">Draft saved.</p>}
        </section>
      </div>
    </div>
  );
}
