// Rarity CRM service worker: installable app shell + offline fallback, and Web Push notifications.
// Bump VERSION to drop old caches; the page shows an "Update available" toast when a new worker is waiting.
const VERSION = "v1";
const STATIC_CACHE = `static-${VERSION}`;
const PAGES_CACHE = `pages-${VERSION}`;
const OFFLINE_URL = "/offline";
const PRECACHE = [OFFLINE_URL, "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((c) => c.addAll(PRECACHE)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keep = [STATIC_CACHE, PAGES_CACHE];
    for (const key of await caches.keys()) if (!keep.includes(key)) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // API calls, auth and live data (calls, inbox) always go to the network untouched.
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  // Hashed build assets never change: cache-first.
  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(cacheFirst(req));
    return;
  }

  // Full page loads: network-first, fall back to the last copy of this page, then the offline page.
  if (req.mode === "navigate") event.respondWith(networkFirstPage(req));
});

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  const res = await fetch(req);
  if (res.ok) (await caches.open(STATIC_CACHE)).put(req, res.clone());
  return res;
}

async function networkFirstPage(req) {
  try {
    const res = await fetch(req);
    // Don't cache login redirects or errors as if they were the page.
    if (res.ok && !res.redirected) (await caches.open(PAGES_CACHE)).put(req, res.clone());
    return res;
  } catch {
    return (await caches.match(req, { cacheName: PAGES_CACHE })) || (await caches.match(OFFLINE_URL));
  }
}

// ---------- push notifications ----------

self.addEventListener("push", (event) => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch { msg = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(msg.title || "Rarity CRM", {
    body: msg.body || "",
    icon: "/icons/icon-192.png",
    tag: msg.tag || undefined,
    renotify: !!msg.tag,
    data: { url: msg.url || "/dashboard" },
  }));
});

// Tap: reuse an open CRM window if there is one, otherwise open the app at the notification's page.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/dashboard", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const win = wins.find((w) => new URL(w.url).origin === self.location.origin);
    if (win) {
      await win.focus();
      return win.navigate(url).catch(() => self.clients.openWindow(url));
    }
    return self.clients.openWindow(url);
  })());
});
