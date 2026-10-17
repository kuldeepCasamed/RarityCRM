"use client";
import { useQueryClient } from "@tanstack/react-query";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { useEffect } from "react";
import { Toaster } from "sonner";
import ServiceWorker from "@/components/ServiceWorker";
import "@/lib/install"; // starts listening for the browser's install prompt as early as possible
import { flushWrites, getQueryClient, PERSIST_MAX_AGE, persister, refreshPending, shouldPersistQuery } from "@/lib/offline";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <PersistQueryClientProvider
      client={getQueryClient()}
      persistOptions={{ persister, maxAge: PERSIST_MAX_AGE, buster: "v1", dehydrateOptions: { shouldDehydrateQuery: shouldPersistQuery } }}
    >
      {children}
      <Toaster richColors position="top-right" />
      <ServiceWorker />
      <OfflineSync />
    </PersistQueryClientProvider>
  );
}

// Replays writes queued while offline: on load and whenever the connection comes back.
function OfflineSync() {
  const qc = useQueryClient();
  useEffect(() => {
    const flush = () => flushWrites(qc);
    refreshPending().then(flush);
    window.addEventListener("online", flush);
    const retry = setInterval(flush, 30_000); // e.g. backend was still waking up on the last attempt
    return () => { window.removeEventListener("online", flush); clearInterval(retry); };
  }, [qc]);
  return null;
}
