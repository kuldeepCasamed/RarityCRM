"use client";
import { CheckCircle2, Copy, Download } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { InstallSteps } from "@/components/InstallApp";
import { promptInstall, useInstallMode } from "@/lib/install";

// Public page to share as an install link (no login needed): shows the right install path for the visitor's device.
export default function InstallPage() {
  const mode = useInstallMode();
  const [done, setDone] = useState(false);

  const install = async () => {
    if (await promptInstall()) setDone(true);
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      toast.success("Link copied: paste it into Safari");
    } catch {
      toast.error("Couldn't copy: long-press the address bar to copy it");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-sm bg-white border rounded-2xl p-6 space-y-5 text-sm text-stone-600">
        <div className="text-center space-y-2">
          {/* eslint-disable-next-line @next/next/no-img-element -- static PWA icon, no optimisation needed */}
          <img src="/icons/icon-192.png" alt="" width={72} height={72} className="mx-auto rounded-2xl border" />
          <h1 className="text-xl font-semibold text-stone-900">Install Rarity CRM</h1>
          <p>Opens like an app from your home screen or desktop, and keeps working when the connection drops.</p>
        </div>

        {done || mode === "installed" ? (
          <div className="text-center space-y-3">
            <CheckCircle2 size={36} className="mx-auto text-green-600" />
            <p className="text-stone-900 font-medium">Rarity CRM is installed on this device.</p>
            <p>Open it from your home screen or apps list.</p>
          </div>
        ) : mode === "prompt" ? (
          <button onClick={install} className="w-full flex items-center justify-center gap-2 bg-brand text-white rounded-lg py-3 text-base font-medium">
            <Download size={18} /> Install app
          </button>
        ) : (
          <>
            <InstallSteps mode={mode} />
            {mode === "ios-other" && (
              <button onClick={copyLink} className="w-full flex items-center justify-center gap-2 border rounded-lg py-3 text-stone-800">
                <Copy size={16} /> Copy link
              </button>
            )}
          </>
        )}

        <Link href="/login" className="block text-center text-brand">Continue in the browser →</Link>
      </div>
    </div>
  );
}
