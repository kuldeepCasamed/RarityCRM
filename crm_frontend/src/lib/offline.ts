"use client";
import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { isServer, QueryClient } from "@tanstack/react-query";
import axios, { isAxiosError } from "axios";
import { createStore, del, get, set, update } from "idb-keyval";
import { useSyncExternalStore } from "react";
import { toast } from "sonner";

// PWA phase 2: React Query cache saved to IndexedDB + a queue for writes made while offline.

const store = typeof indexedDB === "undefined" ? undefined : createStore("rarity-crm", "offline");
const CACHE_KEY = "query-cache";
const QUEUE_KEY = "write-queue";

// ---------- query client + persistence ----------

export const PERSIST_MAX_AGE = 24 * 60 * 60 * 1000;
// Searches, previews and one-off lookups aren't worth keeping offline.
const NO_PERSIST = new Set(["audience", "assign-search", "dups", "rt"]);

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        gcTime: PERSIST_MAX_AGE, // must be >= persist maxAge or restored data is dropped straight away
        // while the backend wakes from sleep it answers 503: keep retrying (~1 min) instead of showing errors
        retry: (count, err) => isAxiosError(err) && err.response?.status === 503 && count < 8,
        retryDelay: 7000,
      },
      // run offline too: queue-able writes get queued, the rest fail with "You're offline" instead of hanging
      mutations: { networkMode: "always" },
    },
  });
}

let browserClient: QueryClient | undefined;
export function getQueryClient() {
  if (isServer) return makeQueryClient();
  return (browserClient ??= makeQueryClient());
}

export const persister = createAsyncStoragePersister({
  key: CACHE_KEY,
  storage: store
    ? { getItem: (k) => get(k, store), setItem: (k, v) => set(k, v, store), removeItem: (k) => del(k, store) }
    : undefined,
});

export const shouldPersistQuery = (q: { queryKey: readonly unknown[]; state: { status: string } }) =>
  q.state.status === "success" && !NO_PERSIST.has(String(q.queryKey[0]));

// ---------- online status ----------

const onlineListeners = (cb: () => void) => {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => { window.removeEventListener("online", cb); window.removeEventListener("offline", cb); };
};
export const useOnline = () => useSyncExternalStore(onlineListeners, () => navigator.onLine, () => true);

// ---------- write queue ----------

type QueuedWrite = { id: string; method: string; url: string; data?: string; label: string; at: number };

let pending = 0;
const queueListeners = new Set<() => void>();
const setPending = (n: number) => { pending = n; queueListeners.forEach((l) => l()); };
export const usePendingWrites = () =>
  useSyncExternalStore((cb) => { queueListeners.add(cb); return () => queueListeners.delete(cb); }, () => pending, () => 0);

const readQueue = async () => (store ? ((await get<QueuedWrite[]>(QUEUE_KEY, store)) ?? []) : []);
export const refreshPending = async () => setPending((await readQueue()).length);

export async function enqueueWrite(w: Omit<QueuedWrite, "id" | "at">) {
  if (!store) throw new Error("Offline storage unavailable");
  await update<QueuedWrite[]>(QUEUE_KEY, (q = []) => [...q, { ...w, id: crypto.randomUUID(), at: Date.now() }], store);
  await refreshPending();
}

// One tab at a time, oldest first. Stops at the first network failure; drops writes the server rejects.
let flushing = false;
export async function flushWrites(qc: QueryClient) {
  if (!store || flushing || !navigator.onLine) return;
  flushing = true;
  const run = async () => {
    let synced = 0;
    const failed: string[] = [];
    for (const w of await readQueue()) {
      try {
        await axios.request({ baseURL: "/api/crm", method: w.method, url: w.url, data: w.data, headers: { "Content-Type": "application/json" } });
        synced++;
      } catch (err) {
        const status = isAxiosError(err) ? err.response?.status : undefined;
        if (!status || status === 503 || status === 401) break; // still offline / backend waking / logged out: retry later
        failed.push(w.label);
      }
      await update<QueuedWrite[]>(QUEUE_KEY, (q = []) => q.filter((x) => x.id !== w.id), store);
    }
    await refreshPending();
    if (synced) {
      toast.success(`Synced ${synced} offline change${synced > 1 ? "s" : ""}`);
      qc.invalidateQueries();
    }
    if (failed.length) toast.error(`${failed.length} offline change${failed.length > 1 ? "s" : ""} could not be saved`, { description: failed.join(", ") });
  };
  try {
    if (navigator.locks) await navigator.locks.request("rarity-write-queue", run);
    else await run();
  } finally {
    flushing = false;
  }
}

// ---------- sign-out ----------

// Wipes everything saved for this user on the device: memory cache, IndexedDB cache + queue, cached pages,
// and the push subscription (so the next person to sign in here doesn't get this user's notifications).
export async function clearOfflineData() {
  browserClient?.clear();
  if (store) await Promise.all([del(CACHE_KEY, store), del(QUEUE_KEY, store)]);
  await refreshPending();
  if ("caches" in window) for (const key of await caches.keys()) if (key.startsWith("pages-")) await caches.delete(key);
  const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
  await (await reg?.pushManager?.getSubscription())?.unsubscribe();
}
