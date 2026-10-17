"use client";
import { Bell } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { dismissPushPrompt, enablePush, getPushState, pushPromptDismissed, resyncPush } from "@/lib/push";

// Asks once per device to turn on notifications (browsers only allow the permission prompt after a click).
export default function PushPrompt() {
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      await resyncPush();
      if ((await getPushState()) !== "off" || Notification.permission !== "default" || pushPromptDismissed()) return;
      // don't offer it until the server has VAPID keys
      const { data } = await api.get<{ configured: boolean }>("/push/config/").catch(() => ({ data: { configured: false } }));
      setShow(data.configured);
    })();
  }, []);

  if (!show) return null;
  const close = () => { dismissPushPrompt(); setShow(false); };
  const enable = async () => {
    setBusy(true);
    try {
      await enablePush();
      toast.success("Notifications turned on for this device");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not turn on notifications");
    }
    setBusy(false);
    close();
  };
  return (
    <div className="flex flex-wrap items-center gap-2 bg-white border-b border-stone-200 px-4 py-2 text-sm">
      <Bell size={16} className="shrink-0 text-brand" />
      <span className="flex-1 min-w-48">Get notified about new leads, replies, due tasks and upcoming appointments.</span>
      <button onClick={enable} disabled={busy} className="bg-brand text-white rounded-md px-3 py-1 disabled:opacity-50">Turn on</button>
      <button onClick={close} className="text-stone-500 px-2">Not now</button>
    </div>
  );
}
