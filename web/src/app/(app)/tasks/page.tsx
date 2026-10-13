"use client";
import Link from "next/link";
import { useState } from "react";
import { Check, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { DemoBadge, EmptyState, ErrorState, FilterSelect, PageHeader, Pagination, PriorityBadge, SearchInput, SortHeader, useListState } from "@/components/common";
import { TaskDialog } from "@/components/forms";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import { PRIORITIES, type Page, type Task } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ReasonBadge } from "@/components/engagement";

export default function TasksPage() {
  const toast = useToast();
  const [s, set] = useListState({ status: "open", mine: "true", sort: "due_at", page: "1" });
  const [editing, setEditing] = useState<Task | undefined>();
  const [open, setOpen] = useState(false);
  const { data, error, mutate, isLoading } = useApi<Page<Task>>("/tasks", {
    q: s.q, status: s.status, priority: s.priority, mine: s.mine === "true", due: s.due, sort: s.sort, page: s.page, page_size: 25,
  });
  const toggle = async (t: Task) => {
    try {
      await api(`/tasks/${t.id}`, { method: "PATCH", json: { status: t.status === "open" ? "done" : "open" } });
      toast("success", t.status === "open" ? "Task completed" : "Task reopened");
      mutate();
    } catch (e) { toast("error", e instanceof Error ? e.message : "Update failed"); }
  };
  return (
    <>
      <PageHeader eyebrow="Follow-ups" title="Tasks & reminders" actions={<Button onClick={() => { setEditing(undefined); setOpen(true); }}><Plus />New task</Button>} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput value={s.q ?? ""} onChange={(q) => set({ q })} placeholder="Search tasks…" />
        <FilterSelect ariaLabel="Whose" allLabel="Everyone's" value={s.mine === "true" ? "true" : ""} onChange={(v) => set({ mine: v || "false" })} options={[{ value: "true", label: "My tasks" }]} />
        <FilterSelect ariaLabel="Status" allLabel="Any status" value={s.status ?? ""} onChange={(status) => set({ status })} options={[{ value: "open", label: "Open" }, { value: "done", label: "Done" }]} />
        <FilterSelect ariaLabel="Due" allLabel="Any due date" value={s.due ?? ""} onChange={(due) => set({ due })} options={[{ value: "overdue", label: "Overdue" }, { value: "today", label: "Due today" }, { value: "week", label: "Due this week" }]} />
        <FilterSelect ariaLabel="Priority" allLabel="Any priority" value={s.priority ?? ""} onChange={(priority) => set({ priority })} options={PRIORITIES} />
      </div>
      {error && <ErrorState error={error} retry={() => mutate()} />}
      <Card>
        <Table>
          <THead><TR className="hover:bg-transparent">
            <TH className="w-10"><span className="sr-only">Done</span></TH>
            <SortHeader field="title" sort={s.sort} onSort={(sort) => set({ sort })}>Task</SortHeader>
            <TH>Related to</TH>
            <SortHeader field="due_at" sort={s.sort} onSort={(sort) => set({ sort })}>Due</SortHeader>
            <SortHeader field="priority" sort={s.sort} onSort={(sort) => set({ sort })}>Priority</SortHeader>
            <TH>Owner</TH>
          </TR></THead>
          <TBody>
            {isLoading && !data && Array.from({ length: 5 }).map((_, i) => <TR key={i}><TD colSpan={6}><Skeleton className="h-5" /></TD></TR>)}
            {data?.items.map((t) => (
              <TR key={t.id}>
                <TD>
                  <button onClick={() => toggle(t)} aria-label={t.status === "open" ? `Complete ${t.title}` : `Reopen ${t.title}`}
                    className={cn("flex size-4 items-center justify-center rounded border", t.status === "done" && "border-primary bg-primary text-primary-foreground")}>
                    {t.status === "done" && <Check className="size-3" />}
                  </button>
                </TD>
                <TD>
                  <button className={cn("text-left font-medium hover:underline", t.status === "done" && "text-muted-foreground line-through")} onClick={() => { setEditing(t); setOpen(true); }}>{t.title}</button>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-muted-foreground"><DemoBadge show={t.is_demo} /><ReasonBadge reason={t.reason} />{t.source_activity_subject && <span className="max-w-72 truncate" title={t.source_activity_subject}>from: {t.source_activity_subject}</span>}</div>
                </TD>
                <TD>{t.lead_id ? <Link href={`/leads/${t.lead_id}`} className="hover:underline">{t.institution_name ?? `Lead #${t.lead_id}`}</Link> : t.institution_id ? <Link href={`/institutions/${t.institution_id}`} className="hover:underline">{t.institution_name}</Link> : <span className="text-muted-foreground">—</span>}</TD>
                <TD className={cn(t.overdue && "font-medium text-red-700")}>{t.due_at ? <><div>{dateTime(t.due_at)}</div><div className="text-xs">{relative(t.due_at)}</div></> : "—"}</TD>
                <TD><PriorityBadge value={t.priority} /></TD>
                <TD className="text-muted-foreground">{t.owner?.full_name ?? "—"}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {data && data.total === 0 && <EmptyState title="No tasks here" description="You're all caught up." />}
        {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => set({ page: String(p) })} />}
      </Card>
      <TaskDialog open={open} onOpenChange={setOpen} initial={editing} onSaved={() => mutate()} />
    </>
  );
}
