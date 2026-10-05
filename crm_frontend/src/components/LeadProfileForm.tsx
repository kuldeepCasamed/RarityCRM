"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api, errMsg, TREATMENTS } from "@/lib/api";

const input = "w-full border rounded-md px-3 py-2 bg-white text-sm mt-1";
const num = (v: FormDataEntryValue | null) => (v === "" || v === null ? null : Number(v));
const str = (v: FormDataEntryValue | null) => (v ?? "") as string;
const date = (v: FormDataEntryValue | null) => (v ? (v as string) : null);

// Each section saves independently with its own PATCH so edits never clobber each other.
export default function LeadProfileForm({ lead }: { lead: any }) {
  const qc = useQueryClient();
  const { data: doctors } = useQuery({ queryKey: ["doctors"], queryFn: async () => (await api.get<any[]>("/doctors/")).data });
  const { data: branches } = useQuery({ queryKey: ["branches"], queryFn: async () => (await api.get<any[]>("/branches/")).data });
  const [interests, setInterests] = useState<string[]>(lead.treatment_interests ?? []);
  const [intl, setIntl] = useState<boolean>(lead.is_international);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: (body: object) => api.patch(`/leads/${lead.id}/`, body),
    onSuccess: () => { setErrors({}); toast.success("Saved"); qc.invalidateQueries({ queryKey: ["lead", String(lead.id)] }); qc.invalidateQueries({ queryKey: ["leads"] }); },
    onError: (e: any) => {
      const d = e?.response?.data;
      if (d && typeof d === "object") setErrors(Object.fromEntries(Object.entries(d).map(([k, v]) => [k, [v].flat().join(" ")])));
      toast.error(errMsg(e));
    },
  });
  const submit = (fn: (f: FormData) => object) => (e: React.FormEvent<HTMLFormElement>) => { e.preventDefault(); save.mutate(fn(new FormData(e.currentTarget))); };
  const Err = ({ k }: { k: string }) => (errors[k] ? <span className="text-xs text-red-600">{errors[k]}</span> : null);
  const Card = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <section className="bg-white border rounded-lg p-4 space-y-3"><h3 className="font-medium">{title}</h3>{children}</section>
  );
  const Save = () => <button disabled={save.isPending} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-60">Save</button>;

  return (
    <div className="space-y-4">
      <form onSubmit={submit((f) => ({ name: str(f.get("name")), email: str(f.get("email")), phone: str(f.get("phone")), country: str(f.get("country")), timezone: str(f.get("timezone")), language: str(f.get("language")), source: f.get("source") }))}>
        <Card title="Contact">
          <div className="grid md:grid-cols-2 gap-3">
            <label className="text-sm">Name<input name="name" required defaultValue={lead.name} className={input} /><Err k="name" /></label>
            <label className="text-sm">Phone<input name="phone" defaultValue={lead.phone} placeholder="+91…" className={input} /><Err k="phone" /></label>
            <label className="text-sm">Email<input name="email" type="email" defaultValue={lead.email} className={input} /><Err k="email" /></label>
            <label className="text-sm">Country<input name="country" defaultValue={lead.country} className={input} /></label>
            <label className="text-sm">Timezone (IANA)<input name="timezone" defaultValue={lead.timezone} placeholder="Europe/London" className={input} /></label>
            <label className="text-sm">Language<input name="language" defaultValue={lead.language} className={input} /></label>
            <label className="text-sm">Source<select name="source" defaultValue={lead.source} className={input}>
              {["website", "google_ads", "meta_ads", "whatsapp", "phone", "walk_in", "referral", "partner", "csv", "manual"].map((s) => <option key={s}>{s}</option>)}</select></label>
          </div>
          <Save />
        </Card>
      </form>

      <form onSubmit={submit((f) => ({ treatment_interests: interests, chief_complaint: str(f.get("chief_complaint")), urgency: str(f.get("urgency")), budget_band: str(f.get("budget_band")), insurance: str(f.get("insurance")), preferred_doctor: num(f.get("preferred_doctor")), preferred_branch: num(f.get("preferred_branch")), preferred_time: str(f.get("preferred_time")), preferred_channel: str(f.get("preferred_channel")) }))}>
        <Card title="Treatment & preferences">
          <div>
            <div className="text-sm">Treatments of interest</div>
            <div className="flex flex-wrap gap-2 mt-1">
              {TREATMENTS.map(([v, l]) => (
                <button type="button" key={v} onClick={() => setInterests((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]))}
                  className={`text-xs rounded-full px-3 py-1 border ${interests.includes(v) ? "bg-brand text-white border-brand" : "bg-white text-stone-600"}`}>{l}</button>
              ))}
            </div>
            <Err k="treatment_interests" />
          </div>
          <label className="text-sm block">Chief complaint / requirement<textarea name="chief_complaint" rows={3} defaultValue={lead.chief_complaint} className={input} /></label>
          <div className="grid md:grid-cols-2 gap-3">
            <label className="text-sm">Urgency<select name="urgency" defaultValue={lead.urgency} className={input}><option value="">—</option><option>routine</option><option>soon</option><option>urgent</option><option>emergency</option></select></label>
            <label className="text-sm">Budget<input name="budget_band" defaultValue={lead.budget_band} className={input} placeholder="e.g. ₹1–2 lakh" /></label>
            <label className="text-sm">Insurance<input name="insurance" defaultValue={lead.insurance} className={input} /></label>
            <label className="text-sm">Preferred contact channel<select name="preferred_channel" defaultValue={lead.preferred_channel} className={input}><option value="">—</option><option>call</option><option>whatsapp</option><option>email</option></select></label>
            <label className="text-sm">Preferred doctor<select name="preferred_doctor" defaultValue={lead.preferred_doctor ?? ""} className={input}><option value="">Any</option>{doctors?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
            <label className="text-sm">Preferred branch<select name="preferred_branch" defaultValue={lead.preferred_branch ?? ""} className={input}><option value="">Any</option>{branches?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
            <label className="text-sm">Preferred time<input name="preferred_time" defaultValue={lead.preferred_time} className={input} placeholder="Evenings, weekends…" /></label>
          </div>
          <Save />
        </Card>
      </form>

      <form onSubmit={submit((f) => ({ is_international: intl, travel_from: date(f.get("travel_from")), travel_to: date(f.get("travel_to")), visa_support_needed: f.get("visa_support_needed") === "on", companions: num(f.get("companions")) ?? 0, facilitator: str(f.get("facilitator")) }))}>
        <Card title="International patient">
          <label className="text-sm flex items-center gap-2"><input type="checkbox" checked={intl} onChange={(e) => setIntl(e.target.checked)} /> Travelling from outside India</label>
          {intl && (
            <div className="grid md:grid-cols-2 gap-3">
              <label className="text-sm">Arrival<input name="travel_from" type="date" defaultValue={lead.travel_from ?? ""} className={input} /></label>
              <label className="text-sm">Departure<input name="travel_to" type="date" defaultValue={lead.travel_to ?? ""} className={input} /><Err k="travel_to" /></label>
              <label className="text-sm">Accompanying persons<input name="companions" type="number" min={0} defaultValue={lead.companions} className={input} /></label>
              <label className="text-sm">Facilitator / agent<input name="facilitator" defaultValue={lead.facilitator} className={input} /></label>
              <label className="text-sm flex items-center gap-2"><input name="visa_support_needed" type="checkbox" defaultChecked={lead.visa_support_needed} /> Needs visa / invitation letter</label>
            </div>
          )}
          <Save />
        </Card>
      </form>

      <form onSubmit={submit((f) => ({ quote_amount: num(f.get("quote_amount")), quote_valid_until: date(f.get("quote_valid_until")), converted_value: num(f.get("converted_value")), lost_reason: str(f.get("lost_reason")), consent_marketing: f.get("consent_marketing") === "on", consent_recording: f.get("consent_recording") === "on", whatsapp_opt_out: f.get("whatsapp_opt_out") === "on", sms_opt_out: f.get("sms_opt_out") === "on" }))}>
        <Card title="Quote & consent">
          <div className="grid md:grid-cols-2 gap-3">
            <label className="text-sm">Quote amount (₹)<input name="quote_amount" type="number" min={0} step="0.01" defaultValue={lead.quote_amount ?? ""} className={input} /><Err k="quote_amount" /></label>
            <label className="text-sm">Quote valid until<input name="quote_valid_until" type="date" defaultValue={lead.quote_valid_until ?? ""} className={input} /></label>
            <label className="text-sm">Converted value (₹)<input name="converted_value" type="number" min={0} step="0.01" defaultValue={lead.converted_value ?? ""} className={input} placeholder="Revenue booked — drives source ROI" /><Err k="converted_value" /></label>
            <label className="text-sm md:col-span-2">Lost / not-interested reason<input name="lost_reason" defaultValue={lead.lost_reason} className={input} /></label>
          </div>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2"><input name="consent_marketing" type="checkbox" defaultChecked={lead.consent_marketing} /> Marketing consent</label>
            <label className="flex items-center gap-2"><input name="consent_recording" type="checkbox" defaultChecked={lead.consent_recording} /> Call-recording consent</label>
            <label className="flex items-center gap-2"><input name="whatsapp_opt_out" type="checkbox" defaultChecked={lead.whatsapp_opt_out} /> WhatsApp opt-out</label>
            <label className="flex items-center gap-2"><input name="sms_opt_out" type="checkbox" defaultChecked={lead.sms_opt_out} /> SMS opt-out</label>
          </div>
          <Save />
        </Card>
      </form>
    </div>
  );
}
