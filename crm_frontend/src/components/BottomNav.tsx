"use client";
import { CheckSquare, Inbox, LayoutDashboard, LogOut, Menu, Users, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import InstallApp from "@/components/InstallApp";
import { useNav } from "@/components/Sidebar";

// Phone navigation (below md): four main tabs + a "More" sheet with everything else. Desktop keeps the Sidebar.
const TABS = [["/dashboard", "Home", LayoutDashboard], ["/leads", "Leads", Users], ["/inbox", "Inbox", Inbox], ["/tasks", "Tasks", CheckSquare]] as const;

const Badge = ({ n }: { n: number }) =>
  n > 0 ? <span className="absolute -top-1 left-1/2 ml-1 rounded-full bg-red-500 text-white text-[10px] leading-4 min-w-4 px-1 text-center">{n}</span> : null;

export default function BottomNav() {
  const path = usePathname();
  const { items, inboxBadge, logout } = useNav();
  const [open, setOpen] = useState(false);
  const more = items.filter(([href]) => !TABS.some(([t]) => t === href));
  const moreActive = more.some(([href]) => path.startsWith(href));

  return (
    <>
      {open && (
        <div className="md:hidden fixed inset-0 z-30 bg-black/40" onClick={() => setOpen(false)}>
          <div className="absolute inset-x-0 bottom-0 rounded-t-2xl bg-white p-4 pb-[calc(1rem+env(safe-area-inset-bottom))]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <span className="font-semibold text-brand">Rarity CRM</span>
              <button onClick={() => setOpen(false)} aria-label="Close" className="p-2 -m-2 text-stone-500"><X size={20} /></button>
            </div>
            <nav className="grid grid-cols-3 gap-2">
              {more.map(([href, label, Icon]) => (
                <Link key={href} href={href} onClick={() => setOpen(false)}
                  className={`flex flex-col items-center gap-1 rounded-lg py-3 text-xs ${path.startsWith(href) ? "bg-brand text-white" : "bg-stone-50 text-stone-700"}`}>
                  <Icon size={20} /> {label}
                </Link>
              ))}
            </nav>
            <InstallApp className="mt-3 w-full flex items-center justify-center gap-2 rounded-lg bg-brand text-white py-3 text-sm" />
            <button onClick={logout} className="mt-3 w-full flex items-center justify-center gap-2 rounded-lg border py-3 text-sm text-stone-600">
              <LogOut size={16} /> Sign out
            </button>
          </div>
        </div>
      )}
      <nav className="md:hidden fixed inset-x-0 bottom-0 z-[5] bg-white border-t border-stone-200 pb-[env(safe-area-inset-bottom)]">
        <div className="grid grid-cols-5">
          {TABS.map(([href, label, Icon]) => (
            <Link key={href} href={href} className={`relative flex flex-col items-center gap-0.5 py-2 text-[11px] ${path.startsWith(href) ? "text-brand font-medium" : "text-stone-500"}`}>
              <Icon size={22} />{label}{href === "/inbox" && <Badge n={inboxBadge} />}
            </Link>
          ))}
          <button onClick={() => setOpen(true)} className={`flex flex-col items-center gap-0.5 py-2 text-[11px] ${moreActive ? "text-brand font-medium" : "text-stone-500"}`}>
            <Menu size={22} />More
          </button>
        </div>
      </nav>
    </>
  );
}
