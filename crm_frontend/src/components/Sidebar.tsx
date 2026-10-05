"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, CalendarDays, CheckSquare, Phone, Columns3, LayoutDashboard, LogOut, Settings, Users } from "lucide-react";

const NAV = [
  ["/dashboard", "Dashboard", LayoutDashboard], ["/pipeline", "Pipeline", Columns3],
  ["/leads", "Leads", Users], ["/appointments", "Appointments", CalendarDays], ["/calls", "Calls", Phone], ["/tasks", "Tasks", CheckSquare], ["/settings", "Settings", Settings],
] as const;

export default function Sidebar() {
  const path = usePathname();
  const router = useRouter();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get("/me/")).data });
  const isManager = me?.role === "admin" || me?.role === "manager";
  const items = isManager ? [...NAV.slice(0, 5), ["/analytics", "Analytics", BarChart3] as const, ...NAV.slice(5)] : NAV;
  const logout = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  };
  return (
    <aside className="w-56 shrink-0 bg-white border-r border-stone-200 flex flex-col">
      <div className="px-5 py-5 text-lg font-semibold text-brand">Rarity CRM</div>
      <nav className="flex-1 px-2 space-y-1">
        {items.map(([href, label, Icon]) => (
          <Link key={href} href={href}
            className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${path.startsWith(href) ? "bg-brand text-white" : "text-stone-700 hover:bg-stone-100"}`}>
            <Icon size={16} /> {label}
          </Link>
        ))}
      </nav>
      <button onClick={logout} className="m-3 flex items-center gap-2 rounded-md px-3 py-2 text-sm text-stone-600 hover:bg-stone-100">
        <LogOut size={16} /> Sign out
      </button>
    </aside>
  );
}
