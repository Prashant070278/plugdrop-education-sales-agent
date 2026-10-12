"use client";
import Link from "next/link";
import { useState } from "react";
import { AlarmClock, ArrowRightLeft, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  DemoBadge, EmptyState, ErrorState, FilterSelect, PageHeader, Pagination, SearchInput, SortHeader, StageBadge, useListState,
} from "@/components/common";
import { LeadDialog, StageDialog } from "@/components/forms";
import { useUser } from "@/components/auth";
import { useApi } from "@/lib/api";
import { compactMoney, date, label, relative } from "@/lib/format";
import { LEAD_STAGES, type Lead, type Page, type UserRef } from "@/lib/types";
import { cn } from "@/lib/utils";

function LeadCard({ lead, onMove }: { lead: Lead; onMove: (l: Lead) => void }) {
  const overdue = lead.next_task_due && new Date(lead.next_task_due) < new Date();
  return (
    <div className="group rounded-lg border bg-background p-3 shadow-sm transition-shadow hover:shadow-panel">
      <Link href={`/leads/${lead.id}`} className="block text-sm font-medium leading-snug hover:underline">{lead.institution.name}</Link>
      <div className="mt-1 truncate text-xs text-muted-foreground">{lead.primary_contact?.full_name ?? "No primary contact"}</div>
      <div className="mt-2.5 flex items-center justify-between gap-2 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="rounded bg-muted px-1.5 py-0.5 font-medium tabular" title="Lead score">{lead.score}</span>
          {Number(lead.open_value) > 0 && <span className="font-medium tabular">{compactMoney(lead.open_value)}</span>}
          <DemoBadge show={lead.is_demo} />
        </span>
        <button onClick={() => onMove(lead)} className="flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-accent hover:text-accent-foreground" aria-label={`Move ${lead.institution.name}`}>
          <ArrowRightLeft className="size-3" />Move
        </button>
      </div>
      {lead.next_task_due && (
        <div className={cn("mt-2 flex items-center gap-1 text-xs", overdue ? "text-red-700" : "text-muted-foreground")}>
          <AlarmClock className="size-3" />Next task {relative(lead.next_task_due)}
        </div>
      )}
    </div>
  );
}

export default function LeadsPage() {
  const me = useUser();
  const [s, set] = useListState({ sort: "-updated_at", page: "1", view: "board" });
  const [creating, setCreating] = useState(false);
  const [moving, setMoving] = useState<Lead | null>(null);
  const { data: owners } = useApi<UserRef[]>("/users/options");
  const owner = s.owner === "me" ? me?.id : s.owner;
  const board = s.view === "board";
  const params = board
    ? { q: s.q, owner_id: owner, demo: s.demo, page_size: 200, sort: "-score" }
    : { q: s.q, owner_id: owner, demo: s.demo, stage: s.stage, sort: s.sort, page: s.page, page_size: 25 };
  const { data, error, mutate, isLoading } = useApi<Page<Lead>>("/leads", params);

  return (
    <>
      <PageHeader eyebrow="Pipeline" title="Leads" description="New → Researched → Qualified → Outreach → Meeting → Proposal → Won."
        actions={<Button onClick={() => setCreating(true)}><Plus />New lead</Button>} />
      <Tabs value={s.view} onValueChange={(view) => set({ view })}>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <TabsList><TabsTrigger value="board">Board</TabsTrigger><TabsTrigger value="table">Table</TabsTrigger></TabsList>
          <SearchInput value={s.q ?? ""} onChange={(q) => set({ q })} placeholder="Search institution…" />
          <FilterSelect ariaLabel="Owner" allLabel="All owners" value={s.owner ?? ""} onChange={(o) => set({ owner: o })}
            options={[{ value: "me", label: "My leads" }, ...(owners ?? []).map((u) => ({ value: String(u.id), label: u.full_name }))]} />
          {!board && <FilterSelect ariaLabel="Stage" allLabel="All stages" value={s.stage ?? ""} onChange={(stage) => set({ stage })} options={LEAD_STAGES} />}
          <FilterSelect ariaLabel="Data" allLabel="Demo + real" value={s.demo ?? ""} onChange={(demo) => set({ demo })} options={[{ value: "false", label: "Real data only" }, { value: "true", label: "Demo only" }]} />
        </div>
        {error && <ErrorState error={error} retry={() => mutate()} />}
        <TabsContent value="board" className="mt-0">
          {isLoading && !data ? <Skeleton className="h-96" /> : (
            <div className="-mx-4 overflow-x-auto px-4 pb-4 sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10">
              <div className="flex min-w-max gap-3">
                {LEAD_STAGES.map((stage) => {
                  const items = data?.items.filter((l) => l.stage === stage) ?? [];
                  return (
                    <section key={stage} className="flex w-64 shrink-0 flex-col rounded-xl bg-muted/60 p-2" aria-label={label(stage)}>
                      <header className="flex items-center justify-between px-1.5 pb-2 pt-1">
                        <StageBadge stage={stage} /><span className="text-xs font-medium text-muted-foreground tabular">{items.length}</span>
                      </header>
                      <div className="grid gap-2">
                        {items.map((l) => <LeadCard key={l.id} lead={l} onMove={setMoving} />)}
                        {items.length === 0 && <div className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">Empty</div>}
                      </div>
                    </section>
                  );
                })}
              </div>
            </div>
          )}
          {data && data.total > data.items.length && <p className="text-xs text-muted-foreground">Showing the top {data.items.length} of {data.total} leads by score. Use the table view to see all.</p>}
        </TabsContent>
        <TabsContent value="table" className="mt-0">
          <Card>
            <Table>
              <THead><TR className="hover:bg-transparent">
                <SortHeader field="institution" sort={s.sort} onSort={(sort) => set({ sort })}>Institution</SortHeader>
                <SortHeader field="stage" sort={s.sort} onSort={(sort) => set({ sort })}>Stage</SortHeader>
                <SortHeader field="score" sort={s.sort} onSort={(sort) => set({ sort })}>Score</SortHeader>
                <TH>Primary contact</TH><TH>Owner</TH><TH className="text-right">Open value</TH>
                <SortHeader field="stage_changed_at" sort={s.sort} onSort={(sort) => set({ sort })}>In stage since</SortHeader>
                <TH />
              </TR></THead>
              <TBody>
                {data?.items.map((l) => (
                  <TR key={l.id}>
                    <TD><Link href={`/leads/${l.id}`} className="font-medium hover:underline">{l.institution.name}</Link> <DemoBadge show={l.is_demo} /></TD>
                    <TD><StageBadge stage={l.stage} /></TD>
                    <TD className="tabular">{l.score}</TD>
                    <TD>{l.primary_contact?.full_name ?? "—"}</TD>
                    <TD className="text-muted-foreground">{l.owner?.full_name ?? "—"}</TD>
                    <TD className="text-right tabular">{Number(l.open_value) ? compactMoney(l.open_value) : "—"}</TD>
                    <TD className="text-muted-foreground">{date(l.stage_changed_at)}</TD>
                    <TD className="text-right"><Button size="sm" variant="ghost" onClick={() => setMoving(l)}><ArrowRightLeft />Move</Button></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {data && data.total === 0 && <EmptyState title="No leads match" />}
            {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => set({ page: String(p) })} />}
          </Card>
        </TabsContent>
      </Tabs>
      <LeadDialog open={creating} onOpenChange={setCreating} onSaved={() => mutate()} />
      {moving && <StageDialog open onOpenChange={(o) => !o && setMoving(null)} lead={moving} onSaved={() => mutate()} />}
    </>
  );
}
