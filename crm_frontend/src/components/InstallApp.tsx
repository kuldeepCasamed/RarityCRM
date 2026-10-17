"use client";
import { Download, MoreVertical, PlusSquare, Share, X } from "lucide-react";
import { useState } from "react";
import { InstallMode, promptInstall, useInstallMode } from "@/lib/install";

// "Install app" button for the sidebar (desktop) and the More sheet (phones). Hidden once running as the app.
export default function InstallApp({ className = "" }: { className?: string }) {
  const mode = useInstallMode();
  const [guide, setGuide] = useState(false);
  if (mode === "installed") return null;

  const onClick = async () => {
    if (mode === "prompt" && (await promptInstall())) return;
    setGuide(true);
  };
  return (
    <>
      <button onClick={onClick} className={className}><Download size={16} /> Install app</button>
      {guide && <InstallGuide mode={mode} onClose={() => setGuide(false)} />}
    </>
  );
}

const Step = ({ n, children }: { n: number; children: React.ReactNode }) => (
  <li className="flex gap-3"><span className="shrink-0 grid place-items-center size-6 rounded-full bg-brand text-white text-xs">{n}</span><span className="pt-0.5">{children}</span></li>
);
const Key = ({ children }: { children: React.ReactNode }) => <span className="inline-flex items-center gap-1 font-medium text-stone-900">{children}</span>;

function InstallGuide({ mode, onClose }: { mode: InstallMode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-40 bg-black/40 grid place-items-end md:place-items-center" onClick={onClose}>
      <div className="w-full md:max-w-sm bg-white rounded-t-2xl md:rounded-xl p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] md:pb-5 space-y-4 text-sm text-stone-600" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-stone-900 text-base">Install Rarity CRM</h2>
          <button onClick={onClose} aria-label="Close" className="p-2 -m-2 text-stone-500"><X size={20} /></button>
        </div>
        <p>Adds Rarity CRM to your home screen / desktop so it opens like an app, works offline, and can show notifications.</p>
        <InstallSteps mode={mode} />
        <button onClick={onClose} className="w-full bg-brand text-white rounded-md py-2">Got it</button>
      </div>
    </div>
  );
}

/** Per-device install instructions (also used by the public /install page). */
export function InstallSteps({ mode }: { mode: InstallMode }) {
  return (
    <>
      {mode === "ios-safari" && (
        <ol className="space-y-3">
          <Step n={1}>Tap <Key><Share size={16} /> Share</Key> in Safari&apos;s toolbar (bottom of the screen, or top on iPad).</Step>
          <Step n={2}>Scroll down and tap <Key><PlusSquare size={16} /> Add to Home Screen</Key>.</Step>
          <Step n={3}>Tap <Key>Add</Key>, then open Rarity CRM from your Home Screen.</Step>
        </ol>
      )}
      {mode === "ios-other" && (
        <ol className="space-y-3">
          <Step n={1}>On iPhone and iPad, apps can only be installed from <Key>Safari</Key>. Copy this page&apos;s address and open it in Safari.</Step>
          <Step n={2}>In Safari tap <Key><Share size={16} /> Share</Key> → <Key><PlusSquare size={16} /> Add to Home Screen</Key>.</Step>
        </ol>
      )}
      {(mode === "manual" || mode === "prompt") && (
        <ol className="space-y-3">
          <Step n={1}><Key>Android (Chrome):</Key> tap <Key><MoreVertical size={16} /></Key> menu → <Key>Install app</Key> or <Key>Add to Home screen</Key>.</Step>
          <Step n={2}><Key>Computer (Chrome / Edge):</Key> click the install icon at the right end of the address bar, or the browser menu → <Key>Install</Key>.</Step>
          <Step n={3}>Don&apos;t see it? It may already be installed (look for <Key>Open in app</Key>), or this browser doesn&apos;t support it (e.g. Firefox on a computer): use Chrome or Edge.</Step>
        </ol>
      )}
    </>
  );
}
