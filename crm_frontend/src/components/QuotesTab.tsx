"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { useState } from "react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";

type Item = { description: string; area: string; quantity: number; unit_price: number | string };
type Quote = {
  id: number; number: string; status: string; currency: string; valid_until: string; discount_type: "percent" | "amount";
  discount_value: string; tax_percent: string; notes: string; terms: string; subtotal: string; discount_amount: string;
  tax_amount: string; total: string; items: (Item & { id: number; line_total: string })[]; created_by_name: string | null; created_at: string;
};
const CURRENCIES = ["INR", "USD", "GBP", "EUR", "AED"];
const BADGE: Record<string, string> = {
  draft: "bg-stone-100 text-stone-600", sent: "bg-sky-100 text-sky-700", accepted: "bg-green-100 text-green-700",
  rejected: "bg-red-100 text-red-700", expired: "bg-amber-100 text-amber-700",
};
const input = "w-full border rounded-md px-2 py-1.5 bg-white text-sm";

export const fmtMoney = (v: number | string, cur = "INR") => {
  const n = Number(v);
  try {
    return new Intl.NumberFormat(cur === "INR" ? "en-IN" : "en-US", { style: "currency", currency: cur, minimumFractionDigits: 2 }).format(n);
  } catch { return `${cur} ${n.toFixed(2)}`; }
};

// Live preview only - the server recalculates and is authoritative. Integer cents avoid float drift.
function preview(items: Item[], dtype: string, dval: number, taxPct: number) {
  const cents = (x: number) => Math.round(x * 100);
  const sub = items.reduce((s, i) => s + cents(Number(i.quantity) * Number(i.unit_price || 0)), 0);
  const disc = Math.min(sub, dtype === "percent" ? Math.round((sub * dval) / 100) : cents(dval));
  const tax = Math.round(((sub - disc) * taxPct) / 100);
  return { sub: sub / 100, disc: disc / 100, tax: tax / 100, total: (sub - disc + tax) / 100 };
}

export default function QuotesTab({ leadId, leadEmail, leadName }: { leadId: number; leadEmail: string; leadName: string }) {
  const qc = useQueryClient();
  const key = ["quotes", String(leadId)];
  const { data: quotes } = useQuery({ queryKey: key, queryFn: async () => (await api.get<Quote[]>(`/leads/${leadId}/quotes/`)).data });
  const [editing, setEditing] = useState<Quote | "new" | null>(null);
  const [emailing, setEmailing] = useState<Quote | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["lead", String(leadId)] }); qc.invalidateQueries({ queryKey: ["history", String(leadId)] }); };

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.post(`/quotes/${id}/status/`, { status }),
    onSuccess: refresh, onError: (e) => toast.error(errMsg(e)),
  });
  const dup = useMutation({ mutationFn: (id: number) => api.post<Quote>(`/quotes/${id}/duplicate/`), onSuccess: (r) => { refresh(); setEditing(r.data); toast.success("Revision created as a draft"); }, onError: (e) => toast.error(errMsg(e)) });
  const del = useMutation({ mutationFn: (id: number) => api.delete(`/quotes/${id}/`), onSuccess: refresh, onError: (e) => toast.error(errMsg(e)) });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-stone-500">Estimates for {leadName}. Sending a quote moves the lead to "Treatment Plan Sent"; accepting moves it to "Treatment Booked".</p>
        <button onClick={() => setEditing("new")} className="bg-brand text-white rounded-md px-3 py-1.5 text-sm shrink-0">+ New quote</button>
      </div>
      {quotes?.map((q) => (
        <div key={q.id} className="bg-white border rounded-lg p-3 text-sm space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <b>{q.number}</b>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${BADGE[q.status]}`}>{q.status}</span>
            <span className="text-stone-500">{q.items.length} item{q.items.length !== 1 && "s"} · valid until {format(new Date(q.valid_until), "d MMM yyyy")}</span>
            <span className="ml-auto font-semibold">{fmtMoney(q.total, q.currency)}</span>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <a href={`/api/crm/quotes/${q.id}/pdf/`} target="_blank" className="border rounded px-2 py-1 bg-white">View PDF</a>
            <a href={`/api/crm/quotes/${q.id}/pdf/?download=1`} className="border rounded px-2 py-1 bg-white">Download</a>
            {["draft", "sent", "accepted"].includes(q.status) && <button onClick={() => setEmailing(q)} className="border rounded px-2 py-1 bg-white">Email to patient</button>}
            {q.status === "draft" && <button onClick={() => setEditing(q)} className="border rounded px-2 py-1 bg-white">Edit</button>}
            {q.status === "draft" && <button onClick={() => setStatus.mutate({ id: q.id, status: "sent" })} className="border rounded px-2 py-1 bg-white" title="Use when you've shared it another way (e.g. WhatsApp)">Mark as sent</button>}
            {q.status === "sent" && <button onClick={() => setStatus.mutate({ id: q.id, status: "accepted" })} className="border border-green-600 text-green-700 rounded px-2 py-1 bg-white">Mark accepted</button>}
            {q.status === "sent" && <button onClick={() => setStatus.mutate({ id: q.id, status: "rejected" })} className="border border-red-500 text-red-600 rounded px-2 py-1 bg-white">Mark rejected</button>}
            <button onClick={() => dup.mutate(q.id)} className="border rounded px-2 py-1 bg-white">{q.status === "draft" ? "Duplicate" : "Revise (copy as draft)"}</button>
            {q.status === "draft" && <button onClick={() => confirm(`Delete draft ${q.number}?`) && del.mutate(q.id)} className="text-red-600 px-2 py-1">Delete</button>}
          </div>
        </div>
      ))}
      {quotes?.length === 0 && <p className="text-stone-400 text-sm">No quotes yet.</p>}
      {editing && <QuoteEditor leadId={leadId} quote={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />}
      {emailing && <EmailDialog quote={emailing} defaultTo={leadEmail} onClose={() => setEmailing(null)} onSent={() => { setEmailing(null); refresh(); }} />}
    </div>
  );
}

function QuoteEditor({ leadId, quote, onClose, onSaved }: { leadId: number; quote: Quote | null; onClose: () => void; onSaved: () => void }) {
  const { data: catalog } = useQuery({ queryKey: ["catalog"], queryFn: async () => (await api.get<any[]>("/treatment-catalog/")).data });
  const [items, setItems] = useState<Item[]>(quote ? quote.items.map(({ description, area, quantity, unit_price }) => ({ description, area, quantity, unit_price })) : [{ description: "", area: "", quantity: 1, unit_price: "" }]);
  const [currency, setCurrency] = useState(quote?.currency ?? "INR");
  const [dtype, setDtype] = useState<"percent" | "amount">(quote?.discount_type ?? "percent");
  const [dval, setDval] = useState(quote?.discount_value ?? "0");
  const [tax, setTax] = useState(quote?.tax_percent ?? "0");
  const [valid, setValid] = useState(quote?.valid_until ?? format(new Date(Date.now() + 30 * 864e5), "yyyy-MM-dd"));
  const [notes, setNotes] = useState(quote?.notes ?? "");
  const [terms, setTerms] = useState(quote?.terms ?? "");
  const [error, setError] = useState("");

  const totals = preview(items, dtype, Number(dval) || 0, Number(tax) || 0);
  const patch = (i: number, p: Partial<Item>) => setItems((cur) => cur.map((it, n) => (n === i ? { ...it, ...p } : it)));

  const save = useMutation({
    mutationFn: () => {
      const body = {
        currency, valid_until: valid, discount_type: dtype, discount_value: dval || "0", tax_percent: tax || "0", notes, terms,
        items: items.filter((i) => i.description.trim()).map((i) => ({ description: i.description.trim(), area: i.area, quantity: Number(i.quantity), unit_price: String(i.unit_price || 0) })),
      };
      return quote ? api.patch(`/quotes/${quote.id}/`, body) : api.post(`/leads/${leadId}/quotes/`, body);
    },
    onSuccess: () => { toast.success("Quote saved"); onSaved(); },
    onError: (e: any) => setError(errMsg(e)),
  });

  return (
    <div className="fixed inset-0 bg-black/40 z-20 overflow-y-auto p-4" onClick={onClose}>
      <div className="bg-white rounded-xl p-5 w-full max-w-3xl mx-auto space-y-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center"><h2 className="font-semibold">{quote ? `Edit ${quote.number}` : "New quote"}</h2><button onClick={onClose} className="text-stone-400">✕</button></div>

        <div className="space-y-2">
          <div className="hidden md:grid grid-cols-[1fr_9rem_4rem_8rem_2rem] gap-2 text-xs text-stone-500"><span>Treatment</span><span>Tooth / area</span><span>Qty</span><span>Unit price</span><span /></div>
          {items.map((it, i) => (
            <div key={i} className="grid md:grid-cols-[1fr_9rem_4rem_8rem_2rem] gap-2">
              <input value={it.description} onChange={(e) => patch(i, { description: e.target.value })} placeholder="Description" className={input} />
              <input value={it.area} onChange={(e) => patch(i, { area: e.target.value })} placeholder="e.g. UR6" className={input} />
              <input type="number" min={1} max={999} value={it.quantity} onChange={(e) => patch(i, { quantity: Number(e.target.value) })} className={input} />
              <input type="number" min={0} step="0.01" value={it.unit_price} onChange={(e) => patch(i, { unit_price: e.target.value })} placeholder="0.00" className={input} />
              <button onClick={() => setItems((cur) => (cur.length > 1 ? cur.filter((_, n) => n !== i) : cur))} className="text-red-500" title="Remove">✕</button>
            </div>
          ))}
          <div className="flex gap-2 flex-wrap">
            <button onClick={() => setItems((c) => [...c, { description: "", area: "", quantity: 1, unit_price: "" }])} className="border rounded-md px-3 py-1.5 text-sm bg-white">+ Add line</button>
            <select value="" onChange={(e) => { const c = catalog?.find((x) => String(x.id) === e.target.value); if (c) setItems((cur) => [...cur.filter((i) => i.description.trim() || Number(i.unit_price)), { description: c.name, area: "", quantity: 1, unit_price: c.default_price }]); }} className="border rounded-md px-2 py-1.5 text-sm bg-white">
              <option value="">+ From price list…</option>
              {catalog?.filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.name} — {fmtMoney(c.default_price)}</option>)}
            </select>
          </div>
        </div>

        <div className="grid md:grid-cols-4 gap-3 text-sm">
          <label>Currency<select value={currency} onChange={(e) => setCurrency(e.target.value)} className={input}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</select></label>
          <label>Discount
            <div className="flex gap-1"><input type="number" min={0} step="0.01" value={dval} onChange={(e) => setDval(e.target.value)} className={input} />
              <select value={dtype} onChange={(e) => setDtype(e.target.value as any)} className="border rounded-md px-1 bg-white"><option value="percent">%</option><option value="amount">amt</option></select></div></label>
          <label>Tax %<input type="number" min={0} max={100} step="0.01" value={tax} onChange={(e) => setTax(e.target.value)} className={input} /></label>
          <label>Valid until<input type="date" value={valid} onChange={(e) => setValid(e.target.value)} className={input} /></label>
        </div>

        <div className="ml-auto w-full md:w-72 text-sm space-y-1">
          <div className="flex justify-between"><span className="text-stone-500">Subtotal</span><span>{fmtMoney(totals.sub, currency)}</span></div>
          {totals.disc > 0 && <div className="flex justify-between"><span className="text-stone-500">Discount</span><span>-{fmtMoney(totals.disc, currency)}</span></div>}
          {totals.tax > 0 && <div className="flex justify-between"><span className="text-stone-500">Tax</span><span>{fmtMoney(totals.tax, currency)}</span></div>}
          <div className="flex justify-between font-semibold text-base border-t pt-1"><span>Total</span><span>{fmtMoney(totals.total, currency)}</span></div>
        </div>

        <label className="text-sm block">Notes to patient (shown on the PDF)<textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className={input} /></label>
        <label className="text-sm block">Terms (leave blank for the standard terms)<textarea value={terms} onChange={(e) => setTerms(e.target.value)} rows={2} className={input} /></label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="border rounded-md px-4 py-2 text-sm">Cancel</button>
          <button disabled={save.isPending || !items.some((i) => i.description.trim())} onClick={() => { setError(""); save.mutate(); }} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-50">{save.isPending ? "Saving…" : "Save draft"}</button>
        </div>
      </div>
    </div>
  );
}

function EmailDialog({ quote, defaultTo, onClose, onSent }: { quote: Quote; defaultTo: string; onClose: () => void; onSent: () => void }) {
  const [to, setTo] = useState(defaultTo);
  const [message, setMessage] = useState("");
  const send = useMutation({
    mutationFn: () => api.post(`/quotes/${quote.id}/email/`, { to, message }),
    onSuccess: () => { toast.success(`Quote ${quote.number} emailed`); onSent(); },
    onError: (e) => toast.error(errMsg(e)),
  });
  return (
    <div className="fixed inset-0 bg-black/40 grid place-items-center z-20" onClick={onClose}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); send.mutate(); }} className="bg-white rounded-xl p-5 w-full max-w-md space-y-3">
        <h2 className="font-semibold">Email {quote.number}</h2>
        <label className="text-sm block">To<input type="email" required value={to} onChange={(e) => setTo(e.target.value)} className={input} placeholder="patient@email.com" /></label>
        <label className="text-sm block">Message (optional)<textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={3} className={input} placeholder="Thank you for choosing Rarity Dental…" /></label>
        <p className="text-xs text-stone-500">The PDF is attached. {quote.status === "draft" && "Sending marks this quote as sent."}</p>
        <div className="flex gap-2 justify-end"><button type="button" onClick={onClose} className="border rounded-md px-4 py-2 text-sm">Cancel</button>
          <button disabled={send.isPending} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-50">{send.isPending ? "Sending…" : "Send"}</button></div>
      </form>
    </div>
  );
}
