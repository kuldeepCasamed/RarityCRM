"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import Link from "next/link";
import { api } from "@/lib/api";

const BUCKETS = [["overdue", "Overdue", "text-red-600"], ["today", "Today", "text-amber-600"], ["upcoming", "Upcoming", "text-stone-700"]] as const;

export default function TasksPage() {
  const qc = useQueryClient();
  const done = useMutation({ mutationFn: (id: number) => api.patch(`/tasks/${id}/`, { is_done: true }), onSuccess: () => qc.invalidateQueries({ queryKey: ["mytasks"] }) });
  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="text-2xl font-semibold">My tasks</h1>
      {BUCKETS.map(([b, label, color]) => <Bucket key={b} bucket={b} label={label} color={color} onDone={(id) => done.mutate(id)} />)}
    </div>
  );
}

function Bucket({ bucket, label, color, onDone }: { bucket: string; label: string; color: string; onDone: (id: number) => void }) {
  const { data } = useQuery({ queryKey: ["mytasks", bucket], queryFn: async () => (await api.get<any[]>("/tasks/", { params: { bucket } })).data });
  return (
    <section>
      <h2 className={`font-medium mb-2 ${color}`}>{label} ({data?.length ?? 0})</h2>
      <div className="space-y-2">
        {data?.map((t) => (
          <div key={t.id} className="bg-white border rounded-lg p-3 text-sm flex items-center gap-3">
            <input type="checkbox" onChange={() => onDone(t.id)} />
            <div><div>{t.title}</div><Link href={`/leads/${t.lead}`} className="text-xs text-brand">{t.lead_name}</Link></div>
            <span className="ml-auto text-xs text-stone-400">{format(new Date(t.due_at), "d MMM HH:mm")}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
