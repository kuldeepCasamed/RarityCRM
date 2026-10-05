"use client";
import { DndContext, DragEndEvent, PointerSensor, useDraggable, useDroppable, useSensor, useSensors } from "@dnd-kit/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { toast } from "sonner";
import ScoreBadge from "@/components/ScoreBadge";
import { api, Lead, Paged, STATUSES } from "@/lib/api";

const COLUMNS = STATUSES.filter(([v]) => !["invalid"].includes(v));

export default function Pipeline() {
  const qc = useQueryClient();
  const key = ["pipeline"];
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const { data } = useQuery({ queryKey: key, queryFn: async () => (await api.get<Paged<Lead>>("/leads/", { params: { page_size: 200, ordering: "-created_at" } })).data });

  const move = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.patch(`/leads/${id}/pipeline/`, { status }),
    onMutate: async ({ id, status }) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Paged<Lead>>(key);
      if (prev) qc.setQueryData(key, { ...prev, results: prev.results.map((l) => (l.id === id ? { ...l, status } : l)) });
      return { prev };
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(key, ctx.prev); toast.error("Could not move lead"); },
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });

  const onEnd = (e: DragEndEvent) => {
    if (!e.over) return;
    const id = Number(e.active.id), status = String(e.over.id);
    const lead = data?.results.find((l) => l.id === id);
    if (lead && lead.status !== status) move.mutate({ id, status });
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Pipeline</h1>
      <DndContext sensors={sensors} onDragEnd={onEnd}>
        <div className="flex gap-3 pb-4">
          {COLUMNS.map(([status, label]) => (
            <Column key={status} id={status} label={label} leads={data?.results.filter((l) => l.status === status) ?? []} />
          ))}
        </div>
      </DndContext>
    </div>
  );
}

function Column({ id, label, leads }: { id: string; label: string; leads: Lead[] }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <div ref={setNodeRef} className={`w-60 shrink-0 rounded-lg p-2 ${isOver ? "bg-stone-200" : "bg-stone-100"}`}>
      <div className="text-xs font-semibold text-stone-600 px-1 pb-2">{label} · {leads.length}</div>
      <div className="space-y-2 min-h-24">{leads.map((l) => <Card key={l.id} lead={l} />)}</div>
    </div>
  );
}

function Card({ lead }: { lead: Lead }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: lead.id });
  return (
    <div ref={setNodeRef} {...listeners} {...attributes}
      style={{ transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined }}
      className={`bg-white rounded-md border p-2 text-sm cursor-grab ${isDragging ? "shadow-lg opacity-80" : ""}`}>
      <Link href={`/leads/${lead.id}`} className="font-medium text-brand" onPointerDown={(e) => e.stopPropagation()}>{lead.name}</Link>
      <div className="flex items-center justify-between"><span className="text-xs text-stone-500">{lead.phone}</span><ScoreBadge score={lead.score} band={lead.score_band} /></div>
      <div className="text-xs text-stone-400">{lead.assigned_to_name ?? "Unassigned"}</div>
    </div>
  );
}
