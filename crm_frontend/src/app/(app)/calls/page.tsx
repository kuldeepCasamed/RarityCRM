"use client";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import Link from "next/link";
import { api, Paged } from "@/lib/api";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export default function CallsPage() {
  const { data } = useQuery({ queryKey: ["calls"], queryFn: async () => (await api.get<Paged<any>>("/calls/")).data });
  return (
    <div className="space-y-4 max-w-4xl">
      <h1 className="text-2xl font-semibold">Calls</h1>
      <div className="bg-white border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500"><tr><th className="p-3">When</th><th>Lead</th><th>By</th><th>Status</th><th>Duration</th><th>Recording</th></tr></thead>
          <tbody>
            {data?.results.map((c) => (
              <tr key={c.id} className="border-t">
                <td className="p-3">{format(new Date(c.created_at), "d MMM HH:mm")}</td>
                <td><Link href={`/leads/${c.lead}`} className="text-brand">{c.lead_name}</Link><div className="text-xs text-stone-400">{c.to_number}</div></td>
                <td>{c.user_name}</td>
                <td>{c.status}{!c.logged && c.status !== "initiated" && <span className="ml-1 text-xs text-amber-600">(not logged)</span>}</td>
                <td>{c.duration_sec ? mmss(c.duration_sec) : "—"}</td>
                <td>{c.has_recording ? <audio controls preload="none" src={`/api/crm/calls/${c.id}/recording/`} className="h-8" /> : "—"}</td>
              </tr>
            ))}
            {data?.results.length === 0 && <tr><td colSpan={6} className="p-6 text-center text-stone-400">No calls yet</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
