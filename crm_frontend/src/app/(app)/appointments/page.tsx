"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addDays, format, startOfDay } from "date-fns";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";

const STATUS = ["booked", "confirmed", "attended", "no_show", "cancelled"];

export default function AppointmentsPage() {
  const qc = useQueryClient();
  const [day, setDay] = useState(startOfDay(new Date()));
  const from = day.toISOString(), to = addDays(day, 7).toISOString();
  const { data } = useQuery({ queryKey: ["appts", from], queryFn: async () => (await api.get<any[]>("/appointments/", { params: { from, to } })).data });
  const setStatus = useMutation({ mutationFn: ({ id, status }: { id: number; status: string }) => api.patch(`/appointments/${id}/`, { status }), onSuccess: () => qc.invalidateQueries({ queryKey: ["appts"] }) });

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Appointments</h1>
        <div className="flex gap-2 text-sm">
          <button onClick={() => setDay(addDays(day, -7))} className="border rounded px-2 py-1 bg-white">‹ Prev week</button>
          <button onClick={() => setDay(startOfDay(new Date()))} className="border rounded px-2 py-1 bg-white">Today</button>
          <button onClick={() => setDay(addDays(day, 7))} className="border rounded px-2 py-1 bg-white">Next week ›</button>
        </div>
      </div>
      <p className="text-sm text-stone-500">{format(day, "d MMM")} – {format(addDays(day, 6), "d MMM yyyy")}</p>
      {data?.length === 0 && <p className="text-stone-400">No appointments this week.</p>}
      {data?.map((a) => (
        <div key={a.id} className="bg-white border rounded-lg p-3 text-sm flex items-center gap-3">
          <div className="w-32 font-medium">{format(new Date(a.start_at), "EEE d MMM HH:mm")}</div>
          <Link href={`/leads/${a.lead}`} className="text-brand">{a.lead_name}</Link>
          <span className="text-stone-400">{a.kind}</span>
          {a.gcal_event_id && <span className="text-xs text-green-700" title="Synced to Google Calendar">📅 synced</span>}
          {a.gcal_sync_error && <span className="text-xs text-red-600" title={a.gcal_sync_error}>⚠ calendar sync failed</span>}
          <select value={a.status} onChange={(e) => setStatus.mutate({ id: a.id, status: e.target.value })} className="ml-auto border rounded px-2 py-1">
            {STATUS.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
      ))}
    </div>
  );
}
