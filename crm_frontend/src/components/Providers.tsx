"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Toaster } from "sonner";

export default function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, retry: false } } }));
  return (
    <QueryClientProvider client={qc}>
      {children}
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  );
}
