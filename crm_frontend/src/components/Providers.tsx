"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Toaster } from "sonner";

export default function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        // while the backend wakes from sleep it answers 503: keep retrying (~1 min) instead of showing errors
        retry: (count, err: any) => err?.response?.status === 503 && count < 8,
        retryDelay: 7000,
      },
    },
  }));
  return (
    <QueryClientProvider client={qc}>
      {children}
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  );
}
