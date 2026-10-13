"use client";
import { ArrowRightLeft, CalendarCheck, CheckSquare, Network, Mail, MessageCircle, MessageSquareReply, Microscope, Phone, StickyNote } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/lib/api";
import { dateTime, label } from "@/lib/format";
import type { Activity, ActivityType, AuditEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

const ICON: Record<ActivityType, React.ElementType> = {
  research: Microscope, call: Phone, email: Mail, reply: MessageSquareReply, meeting: CalendarCheck,
  task: CheckSquare, status_change: ArrowRightLeft, note: StickyNote, whatsapp: MessageCircle, linkedin: Network,
};

export function Timeline({ items }: { items: Activity[] | undefined }) {
  if (!items) return <div className="grid gap-3"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>;
  if (items.length === 0) return <p className="text-sm text-muted-foreground">No activity yet.</p>;
  return (
    <ol className="relative grid gap-5 before:absolute before:bottom-2 before:left-[15px] before:top-2 before:w-px before:bg-border">
      {items.map((a) => {
        const Icon = ICON[a.activity_type] ?? StickyNote;
        const status = a.activity_type === "status_change";
        return (
          <li key={a.id} className="relative flex gap-3">
            <span className={cn("z-10 flex size-8 shrink-0 items-center justify-center rounded-full border bg-background",
              status ? "border-pdpurple/40 text-pdpurple-deep" : "text-muted-foreground")}>
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1 pt-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <span className="text-sm font-medium">{a.subject}</span>
                <time className="text-xs text-muted-foreground tabular" dateTime={a.occurred_at}>{dateTime(a.occurred_at)}</time>
              </div>
              <div className="text-xs text-muted-foreground">
                {label(a.activity_type)} · {a.user?.full_name ?? "System"}{a.contact ? ` · with ${a.contact.full_name}` : ""}{a.is_demo ? " · demo" : ""}
              </div>
              {a.body && <p className="mt-1.5 whitespace-pre-wrap rounded-md bg-muted/60 p-2.5 text-sm">{a.body}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ") || "—";
  return String(v);
}

export function AuditList({ entity, id }: { entity: string; id: number }) {
  const { data } = useApi<AuditEvent[]>(`/audit/entity/${entity}/${id}`);
  if (!data) return <Skeleton className="h-16" />;
  if (data.length === 0) return <p className="text-sm text-muted-foreground">No changes recorded.</p>;
  return (
    <ul className="grid gap-3 text-sm">
      {data.map((e) => (
        <li key={e.id} className="rounded-md border p-3">
          <div className="flex justify-between gap-2"><span className="font-medium">{label(e.action)}</span><span className="text-xs text-muted-foreground">{e.actor?.full_name ?? "System"} · {dateTime(e.created_at)}</span></div>
          {e.action !== "create" && e.action !== "delete" && (
            <ul className="mt-1.5 grid gap-0.5 text-xs text-muted-foreground">
              {Object.entries(e.changes).map(([k, v]) => {
                const c = v as { from?: unknown; to?: unknown };
                return <li key={k}><span className="font-medium text-foreground">{label(k)}</span>: {fmt(c?.from)} → {fmt(c?.to)}</li>;
              })}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}
