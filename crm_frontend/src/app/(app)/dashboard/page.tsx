"use client";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import Link from "next/link";
import ScoreBadge from "@/components/ScoreBadge";
import { api, statusLabel } from "@/lib/api";

type Dash = {
  kpis: Record<string, number>;
  funnel: { status: string; count: number }[];
  sources: { source: string; count: number }[];
  today_appointments: number;
  hot_leads: { id: number; name: string; score: number; status: string; assigned_to_name: string | null }[];
};

const KPI_LABELS: Record<string, string> = {
  new_leads: "New leads (30d)", converted: "Converted", conversion_rate: "Conversion %",
  unassigned: "Unassigned new", my_open_tasks: "My open tasks", my_overdue_tasks: "My overdue tasks",
};

export default function Dashboard() {
  const { data } = useQuery({ queryKey: ["dash"], queryFn: async () => (await api.get<Dash>("/analytics/dashboard/")).data });
  const { data: rt } = useQuery({ queryKey: ["rt"], queryFn: async () => (await api.get("/analytics/response-time/")).data });
  if (!data) return <p className="text-stone-500">Loading…</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {Object.entries(data.kpis).map(([k, v]) => (
          <div key={k} className="bg-white border border-stone-200 rounded-lg p-4">
            <div className="text-xs text-stone-500">{KPI_LABELS[k] ?? k}</div>
            <div className="text-2xl font-semibold">{v}</div>
          </div>
        ))}
        <div className="bg-white border border-stone-200 rounded-lg p-4">
          <div className="text-xs text-stone-500">Appointments today</div>
          <div className="text-2xl font-semibold">{data.today_appointments}</div>
        </div>
        <div className="bg-white border border-stone-200 rounded-lg p-4">
          <div className="text-xs text-stone-500">Avg first response (min)</div>
          <div className="text-2xl font-semibold">{rt?.avg_response_minutes ?? "—"}</div>
        </div>
      </div>
      <div className="bg-white border border-stone-200 rounded-lg p-4">
        <h2 className="font-medium mb-2">🔥 Hot leads to call first</h2>
        {data.hot_leads.length === 0 && <p className="text-sm text-stone-400">No hot leads right now.</p>}
        <div className="divide-y">
          {data.hot_leads.map((l) => (
            <div key={l.id} className="flex items-center gap-3 py-2 text-sm">
              <Link href={`/leads/${l.id}`} className="font-medium text-brand">{l.name}</Link>
              <span className="text-stone-500">{statusLabel(l.status)}</span>
              <span className="text-stone-400">{l.assigned_to_name ?? "Unassigned"}</span>
              <span className="ml-auto"><ScoreBadge score={l.score} band="hot" /></span>
            </div>
          ))}
        </div>
      </div>
      <div className="grid lg:grid-cols-2 gap-6">
        <Chart title="Funnel" data={data.funnel.map((f) => ({ name: statusLabel(f.status), count: f.count }))} />
        <Chart title="Sources" data={data.sources.map((s) => ({ name: s.source, count: s.count }))} />
      </div>
    </div>
  );
}

function Chart({ title, data }: { title: string; data: { name: string; count: number }[] }) {
  return (
    <div className="bg-white border border-stone-200 rounded-lg p-4">
      <h2 className="font-medium mb-2">{title}</h2>
      <div className="h-64">
        <ResponsiveContainer>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="name" fontSize={11} interval={0} angle={-20} height={60} textAnchor="end" />
            <YAxis allowDecimals={false} fontSize={11} />
            <Tooltip />
            <Bar dataKey="count" fill="#8c7864" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
