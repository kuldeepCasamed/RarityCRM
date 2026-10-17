"use client";
import { WifiOff } from "lucide-react";

export default function OfflinePage() {
  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="max-w-sm text-center space-y-4">
        <WifiOff size={40} className="mx-auto text-brand" />
        <h1 className="text-xl font-semibold">You&apos;re offline</h1>
        <p className="text-sm text-stone-600">Rarity CRM needs a connection to load this page. Check your network and try again.</p>
        <button onClick={() => location.reload()} className="rounded-md bg-brand px-4 py-2 text-sm text-white">
          Retry
        </button>
      </div>
    </div>
  );
}
