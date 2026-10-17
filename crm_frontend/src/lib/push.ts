"use client";
import { api } from "@/lib/api";

// PWA phase 3: Web Push subscription for this device. The service worker (public/sw.js) shows the notifications.

export type PushState = "unsupported" | "ios-install" | "denied" | "off" | "on";

// The user switched notifications off in Settings: don't silently turn them back on at the next login.
const OPT_OUT_KEY = "rarity-push-opt-out";
const storage = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string | null) => { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch {} },
};

const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;

// The service worker is only registered in production builds (see ServiceWorker.tsx).
const supported = () =>
  process.env.NODE_ENV === "production" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

async function currentSubscription() {
  const reg = await navigator.serviceWorker.getRegistration();
  return (await reg?.pushManager.getSubscription()) ?? null;
}

export async function getPushState(): Promise<PushState> {
  if (!supported()) return isIOS() && !isStandalone() ? "ios-install" : "unsupported";
  if (Notification.permission === "denied") return "denied";
  return (await currentSubscription()) ? "on" : "off";
}

const b64ToBytes = (b64: string) => {
  const raw = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};
const bytesToB64 = (buf: ArrayBuffer) =>
  btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** Ask permission (if needed), subscribe this device and register it with the backend. Throws a user-facing message. */
export async function enablePush() {
  const { data } = await api.get<{ configured: boolean; public_key: string }>("/push/config/");
  if (!data.configured) throw new Error("Notifications aren't set up on the server yet.");
  if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications are blocked for this site in your browser settings.");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  const key = sub?.options.applicationServerKey;
  if (sub && key && bytesToB64(key) !== data.public_key) { await sub.unsubscribe(); sub = null; } // server keys were rotated
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(data.public_key) });
  await api.post("/push/subscribe/", sub.toJSON());
  storage.set(OPT_OUT_KEY, null);
}

/** Stop notifications on this device. `server: false` when the session is already gone (nothing to tell the backend). */
export async function disablePush({ server = true, optOut = false } = {}) {
  if (optOut) storage.set(OPT_OUT_KEY, "1");
  if (!supported()) return;
  const sub = await currentSubscription();
  if (!sub) return;
  if (server) await api.delete("/push/subscribe/", { data: { endpoint: sub.endpoint } }).catch(() => {});
  await sub.unsubscribe();
}

/** After sign-in: if this browser already allows notifications, (re)subscribe for the signed-in user without prompting. */
export async function resyncPush() {
  if (!supported() || Notification.permission !== "granted" || storage.get(OPT_OUT_KEY)) return;
  await enablePush().catch(() => {});
}

export const pushPromptDismissed = () => !!storage.get("rarity-push-prompt-dismissed");
export const dismissPushPrompt = () => storage.set("rarity-push-prompt-dismissed", "1");
