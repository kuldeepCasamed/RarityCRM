"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { clearOfflineData, usePendingWrites } from "@/lib/offline";
import { disablePush } from "@/lib/push";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, Inbox, Megaphone, Repeat, CalendarDays, CheckSquare, Phone, Columns3, LayoutDashboard, LogOut, Settings, Users } from "lucide-react";

const NAV = [
  ["/dashboard", "Dashboard", LayoutDashboard], ["/pipeline", "Pipeline", Columns3],
  ["/leads", "Leads", Users], ["/inbox", "Inbox", Inbox], ["/sequences", "Sequences", Repeat], ["/appointments", "Appointments", CalendarDays], ["/calls", "Calls", Phone], ["/tasks", "Tasks", CheckSquare], ["/settings", "Settings", Settings],
] as const;

// Shared by the desktop sidebar and the phone bottom bar.
export function useNav() {
  const router = useRouter();
  const pending = usePendingWrites();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const { data: counts } = useQuery({ queryKey: ["inbox-count"], queryFn: async () => (await api.get("/inbox/count/")).data, refetchInterval: 60_000 });
  const inboxBadge = (counts?.needs_reply ?? 0) + (counts?.unmatched ?? 0);
  const isManager = me?.role === "admin" || me?.role === "manager";
  const items = isManager ? [...NAV.slice(0, 5), ["/campaigns", "Campaigns", Megaphone] as const, ["/analytics", "Analytics", BarChart3] as const, ...NAV.slice(5)] : NAV;
  const logout = async () => {
    if (pending > 0 && !confirm(`${pending} offline change${pending > 1 ? "s haven't" : " hasn't"} synced yet and will be lost. Sign out anyway?`)) return;
    await disablePush(); // while still signed in, so the backend forgets this device too
    await fetch("/api/auth/logout", { method: "POST" });
    await clearOfflineData(); // nothing from this user's session stays on the device
    router.push("/login");
  };
  return { items, inboxBadge, logout };
}

export default function Sidebar() {
  const path = usePathname();
  const { items, inboxBadge, logout } = useNav();
  return (
    <aside className="hidden md:flex w-56 shrink-0 bg-white border-r border-stone-200 flex-col">
      <div className="px-5 py-5 text-lg font-semibold text-brand">Rarity CRM</div>
      <nav className="flex-1 px-2 space-y-1">
        {items.map(([href, label, Icon]) => (
          <Link key={href} href={href}
            className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${path.startsWith(href) ? "bg-brand text-white" : "text-stone-700 hover:bg-stone-100"}`}>
            <Icon size={16} /> {label}{href === "/inbox" && inboxBadge > 0 && <span className="ml-auto rounded-full bg-red-500 text-white text-xs px-1.5">{inboxBadge}</span>}
          </Link>
        ))}
      </nav>
      <button onClick={logout} className="m-3 flex items-center gap-2 rounded-md px-3 py-2 text-sm text-stone-600 hover:bg-stone-100">
        <LogOut size={16} /> Sign out
      </button>
    </aside>
  );
}
