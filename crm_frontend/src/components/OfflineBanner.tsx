"use client";
import { CloudUpload, WifiOff } from "lucide-react";
import { useOnline, usePendingWrites } from "@/lib/offline";

export default function OfflineBanner() {
  const online = useOnline();
  const pending = usePendingWrites();
  const waiting = pending > 0 ? ` ${pending} change${pending > 1 ? "s" : ""} waiting to sync.` : "";
  if (!online)
    return (
      <div className="flex items-center gap-2 bg-amber-100 text-amber-900 border-b border-amber-200 px-4 py-2 text-sm">
        <WifiOff size={16} className="shrink-0" />
        <span>You&apos;re offline — showing saved data. Notes, tasks, appointment and pipeline changes will sync when you reconnect.{waiting}</span>
      </div>
    );
  if (pending > 0)
    return (
      <div className="flex items-center gap-2 bg-sky-50 text-sky-900 border-b border-sky-200 px-4 py-2 text-sm">
        <CloudUpload size={16} className="shrink-0" /> Syncing {pending} offline change{pending > 1 ? "s" : ""}…
      </div>
    );
  return null;
}
