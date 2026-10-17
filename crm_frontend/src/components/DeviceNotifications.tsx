"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { disablePush, enablePush, getPushState, PushState } from "@/lib/push";

const HINT: Record<PushState, string> = {
  on: "This device gets notifications for new leads, replies, due tasks and upcoming appointments.",
  off: "Turn on to get notified about new leads, replies, due tasks and upcoming appointments.",
  denied: "Notifications are blocked for this site. Allow them in your browser's site settings, then reload.",
  "ios-install": "On iPhone/iPad, first add Rarity CRM to your Home Screen (Share → Add to Home Screen), then open it from there.",
  unsupported: "This browser doesn't support notifications.",
};

export default function DeviceNotifications() {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { getPushState().then(setState); }, []);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); toast.success(ok); } catch (e) { toast.error(e instanceof Error && !("response" in e) ? e.message : errMsg(e)); }
    setState(await getPushState());
    setBusy(false);
  };
  const test = async () => {
    try { toast.info((await api.post<{ result: string }>("/push/test/")).data.result); } catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <section className="bg-white border rounded-lg p-4 space-y-3">
      <h2 className="font-medium">Notifications on this device</h2>
      <p className="text-sm text-stone-500">{state ? HINT[state] : "Checking…"}</p>
      <div className="flex gap-2">
        {state === "off" && <button disabled={busy} onClick={() => run(enablePush, "Notifications turned on")} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-50">Turn on</button>}
        {state === "on" && <>
          <button disabled={busy} onClick={test} className="border rounded-md px-4 py-2 text-sm">Send test</button>
          <button disabled={busy} onClick={() => run(() => disablePush({ optOut: true }), "Notifications turned off")} className="text-stone-600 px-2 text-sm disabled:opacity-50">Turn off</button>
        </>}
      </div>
    </section>
  );
}
