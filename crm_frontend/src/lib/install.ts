"use client";
import { useSyncExternalStore } from "react";

// "Install app" support. Chrome/Edge/Android fire `beforeinstallprompt`, which we keep so a button can open the
// browser's install dialog later. iOS has no such event: Safari users get Add-to-Home-Screen steps instead.

export type InstallMode =
  | "installed" // running as the installed app: nothing to offer
  | "prompt" // the browser's own install dialog is available
  | "ios-safari" // Share → Add to Home Screen
  | "ios-other" // iOS Chrome/Firefox/…: must open in Safari first
  | "manual"; // anything else: point at the browser menu

type PromptEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> };

let deferred: PromptEvent | null = null;
let installed = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

// Registered at import time (Providers imports this early): the event can fire before any component mounts.
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => { deferred = e as PromptEvent; notify(); });
  window.addEventListener("appinstalled", () => { deferred = null; installed = true; notify(); });
}

const isStandalone = () => matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
export const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isIOSSafari = () => isIOS() && !/CriOS|FxiOS|EdgiOS|OPiOS|GSA\//.test(navigator.userAgent);

function mode(): InstallMode {
  if (installed || isStandalone()) return "installed";
  if (deferred) return "prompt";
  if (isIOS()) return isIOSSafari() ? "ios-safari" : "ios-other";
  return "manual";
}

export const useInstallMode = () =>
  useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb); }, mode, () => "installed" as InstallMode);

/** Opens the browser's install dialog. Returns true if the user accepted. */
export async function promptInstall() {
  if (!deferred) return false;
  const e = deferred;
  deferred = null; // a prompt event can only be used once
  await e.prompt();
  const { outcome } = await e.userChoice;
  notify();
  return outcome === "accepted";
}
