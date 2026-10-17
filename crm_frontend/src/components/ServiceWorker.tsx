"use client";
import { useEffect } from "react";
import { toast } from "sonner";

// Registers /sw.js in production and offers a reload when a new version is waiting.
export default function ServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    let reloading = false;
    // The first install also fires controllerchange (clients.claim); only an update should reload the page.
    const hadController = !!navigator.serviceWorker.controller;
    const onControllerChange = () => {
      if (reloading || !hadController) return;
      reloading = true;
      location.reload();
    };
    navigator.serviceWorker.addEventListener("controllerchange", onControllerChange);

    const promptUpdate = (worker: ServiceWorker) =>
      toast("Update available", {
        duration: Infinity,
        action: { label: "Reload", onClick: () => worker.postMessage("SKIP_WAITING") },
      });

    navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).then((reg) => {
      // Only prompt when replacing an existing worker, not on first install.
      if (reg.waiting && navigator.serviceWorker.controller) promptUpdate(reg.waiting);
      reg.addEventListener("updatefound", () => {
        const next = reg.installing;
        next?.addEventListener("statechange", () => {
          if (next.state === "installed" && navigator.serviceWorker.controller) promptUpdate(next);
        });
      });
    }).catch(() => {});

    return () => navigator.serviceWorker.removeEventListener("controllerchange", onControllerChange);
  }, []);
  return null;
}
