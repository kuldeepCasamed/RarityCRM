"use client";
import { useQueryClient } from "@tanstack/react-query";
import { Mic, MicOff, Phone, PhoneOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";

type Phase = "idle" | "connecting" | "ringing" | "in-call" | "wrapup";
const OUTCOMES = [["connected", "Connected"], ["interested", "Interested"], ["callback_requested", "Callback requested"], ["not_interested", "Not interested"], ["no_answer", "No answer"], ["busy", "Busy"], ["voicemail", "Voicemail"], ["wrong_number", "Wrong number"]];

export default function CallPanel({ leadId, leadName, phone }: { leadId: number; leadName: string; phone: string }) {
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>("idle");
  const [muted, setMuted] = useState(false);
  const [secs, setSecs] = useState(0);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const deviceRef = useRef<any>(null);
  const callRef = useRef<any>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (timer.current) clearInterval(timer.current); deviceRef.current?.destroy(); }, []);

  const startTimer = () => { setSecs(0); timer.current = setInterval(() => setSecs((s) => s + 1), 1000); };
  const stopTimer = () => { if (timer.current) clearInterval(timer.current); timer.current = null; };

  async function getDevice() {
    if (deviceRef.current) return deviceRef.current;
    const { Device, Call } = await import("@twilio/voice-sdk");
    const { data } = await api.get<{ token: string }>("/calls/token/");
    const device = new Device(data.token, { codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU] });
    device.on("tokenWillExpire", async () => device.updateToken((await api.get("/calls/token/")).data.token));
    device.on("error", (e: any) => toast.error(`Call error: ${e.message}`));
    deviceRef.current = device;
    return device;
  }

  async function startCall() {
    setPhase("connecting");
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true }); // surface mic permission early
      const device = await getDevice();
      const { data } = await api.post<{ id: number }>("/calls/initiate/", { lead_id: leadId });
      setSessionId(data.id);
      // `To` is informational only: the server dials the number stored on the session.
      const call = await device.connect({ params: { To: phone, SessionId: String(data.id) } });
      callRef.current = call;
      setPhase("ringing");
      call.on("accept", () => { setPhase("in-call"); startTimer(); });
      call.on("disconnect", () => finish(data.id));
      call.on("cancel", () => finish(data.id));
      call.on("error", (e: any) => { toast.error(e.message); finish(data.id); });
    } catch (e: any) {
      toast.error(e?.response ? errMsg(e) : e?.message ?? "Could not start call");
      setPhase("idle");
    }
  }

  async function finish(id: number) {
    stopTimer();
    callRef.current = null;
    setMuted(false);
    // let Twilio's status callback land before the outcome form opens
    for (let i = 0; i < 8; i++) {
      const { data } = await api.get(`/calls/${id}/`);
      if (["completed", "busy", "no-answer", "failed", "canceled"].includes(data.status)) break;
      await new Promise((r) => setTimeout(r, 1500));
    }
    setPhase("wrapup");
  }

  const hangUp = () => callRef.current?.disconnect();
  const toggleMute = () => { const m = !muted; callRef.current?.mute(m); setMuted(m); };
  const mmss = `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;

  async function logCall(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const fu = f.get("next_followup_at") as string;
    try {
      await api.post(`/calls/${sessionId}/log/`, { outcome: f.get("outcome"), notes: f.get("notes"), next_followup_at: fu ? new Date(fu).toISOString() : undefined });
      toast.success("Call logged");
      ["lead", "contacts", "calls", "leads"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      setPhase("idle");
    } catch (err) { toast.error(errMsg(err)); }
  }

  if (phase === "idle")
    return <button onClick={startCall} disabled={!phone} title={phone ? "" : "No phone number"} className="flex items-center gap-2 bg-green-600 text-white rounded-md px-3 py-2 text-sm disabled:opacity-40"><Phone size={16} /> Call</button>;

  if (phase === "wrapup")
    return (
      <div className="fixed inset-0 bg-black/40 grid place-items-center z-20">
        <form onSubmit={logCall} className="bg-white rounded-xl p-6 w-full max-w-md space-y-3">
          <h2 className="font-semibold">Log call with {leadName}</h2>
          <label className="text-sm block">Outcome<select name="outcome" required className="w-full border rounded-md px-3 py-2 mt-1" defaultValue="">
            <option value="" disabled>Select…</option>{OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <label className="text-sm block">Notes<textarea name="notes" rows={3} className="w-full border rounded-md px-3 py-2 mt-1" /></label>
          <label className="text-sm block">Next follow-up<input name="next_followup_at" type="datetime-local" className="w-full border rounded-md px-3 py-2 mt-1" /></label>
          <button className="w-full bg-brand text-white rounded-md py-2">Save</button>
        </form>
      </div>
    );

  return (
    <div className="flex items-center gap-3 bg-stone-900 text-white rounded-lg px-4 py-2 text-sm">
      <span className="animate-pulse">●</span>
      <span>{phase === "connecting" ? "Connecting…" : phase === "ringing" ? "Ringing…" : `On call ${mmss}`}</span>
      {phase === "in-call" && <button onClick={toggleMute} className="p-1.5 rounded bg-stone-700">{muted ? <MicOff size={16} /> : <Mic size={16} />}</button>}
      <button onClick={hangUp} className="p-1.5 rounded bg-red-600"><PhoneOff size={16} /></button>
    </div>
  );
}
