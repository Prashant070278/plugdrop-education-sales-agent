"use client";
import Link from "next/link";
import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DemoBadge, EmptyState, ErrorState, FilterSelect, PageHeader, Pagination, SearchInput, SortHeader, StageBadge, useListState } from "@/components/common";
import { OpportunityDialog } from "@/components/forms";
import { useUser } from "@/components/auth";
import { useApi } from "@/lib/api";
import { compactMoney, date, label, money } from "@/lib/format";
import { OPP_STAGES, type Opportunity, type Page, type UserRef } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function OpportunitiesPage() {
  const me = useUser();
  const [s, set] = useListState({ view: "board", sort: "-estimated_value", page: "1" });
  const [editing, setEditing] = useState<Opportunity | undefined>();
  const { data: owners } = useApi<UserRef[]>("/users/options");
  const owner = s.owner === "me" ? me?.id : s.owner;
  const board = s.view === "board";
  const { data, error, mutate, isLoading } = useApi<Page<Opportunity>>("/opportunities", board
    ? { q: s.q, owner_id: owner, page_size: 200, sort: "-estimated_value" }
    : { q: s.q, owner_id: owner, stage: s.stage, sort: s.sort, page: s.page, page_size: 25 });

  const totals = (stage: string) => (data?.items ?? []).filter((o) => o.stage === stage).reduce((a, o) => a + Number(o.estimated_value), 0);
  const today = new Date().toISOString().slice(0, 10);

  return (
    <>
      <PageHeader eyebrow="Pipeline" title="Opportunities" description="Deals with value, solution interest, decision dates and next actions. Create them from a lead." />
      <Tabs value={s.view} onValueChange={(view) => set({ view })}>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <TabsList><TabsTrigger value="board">Board</TabsTrigger><TabsTrigger value="table">Table</TabsTrigger></TabsList>
          <SearchInput value={s.q ?? ""} onChange={(q) => set({ q })} placeholder="Search opportunity, institution…" />
          <FilterSelect ariaLabel="Owner" allLabel="All owners" value={s.owner ?? ""} onChange={(o) => set({ owner: o })}
            options={[{ value: "me", label: "Mine" }, ...(owners ?? []).map((u) => ({ value: String(u.id), label: u.full_name }))]} />
          {!board && <FilterSelect ariaLabel="Stage" allLabel="All stages" value={s.stage ?? ""} onChange={(stage) => set({ stage })} options={OPP_STAGES} />}
        </div>
        {error && <ErrorState error={error} retry={() => mutate()} />}
        <TabsContent value="board" className="mt-0">
          {isLoading && !data ? <Skeleton className="h-96" /> : (
            <div className="-mx-4 overflow-x-auto px-4 pb-4 sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10">
              <div className="flex min-w-max gap-3">
                {OPP_STAGES.map((stage) => {
                  const items = data?.items.filter((o) => o.stage === stage) ?? [];
                  return (
                    <section key={stage} aria-label={label(stage)} className="flex w-72 shrink-0 flex-col rounded-xl bg-muted/60 p-2">
                      <header className="flex items-center justify-between px-1.5 pb-2 pt-1">
                        <span className="flex items-center gap-2"><StageBadge stage={stage} /><span className="text-xs text-muted-foreground">{items.length}</span></span>
                        <span className="text-xs font-medium tabular">{compactMoney(totals(stage))}</span>
                      </header>
                      <div className="grid gap-2">
                        {items.map((o) => (
                          <button key={o.id} onClick={() => setEditing(o)} className="rounded-lg border bg-background p-3 text-left shadow-sm transition-shadow hover:shadow-panel">
                            <div className="text-sm font-medium leading-snug">{o.institution.name}</div>
                            <div className="mt-1 flex items-center justify-between text-xs"><span className="font-semibold tabular">{money(o.estimated_value, o.currency)}</span><DemoBadge show={o.is_demo} /></div>
                            <div className="mt-1 text-xs text-muted-foreground">{o.solution_interest.map(label).join(" · ")}</div>
                            {o.next_action && <div className={cn("mt-1.5 text-xs", o.next_action_date && o.next_action_date < today && !["won", "lost"].includes(o.stage) ? "text-red-700" : "text-muted-foreground")}>Next: {o.next_action}{o.next_action_date ? ` · ${date(o.next_action_date)}` : ""}</div>}
                          </button>
                        ))}
                        {items.length === 0 && <div className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">Empty</div>}
                      </div>
                    </section>
                  );
                })}
              </div>
            </div>
          )}
        </TabsContent>
        <TabsContent value="table" className="mt-0">
          <Card>
            <Table>
              <THead><TR className="hover:bg-transparent">
                <SortHeader field="institution" sort={s.sort} onSort={(sort) => set({ sort })}>Institution</SortHeader>
                <SortHeader field="stage" sort={s.sort} onSort={(sort) => set({ sort })}>Stage</SortHeader>
                <SortHeader field="estimated_value" sort={s.sort} onSort={(sort) => set({ sort })} className="text-right">Value</SortHeader>
                <TH>Solutions</TH>
                <SortHeader field="expected_decision_date" sort={s.sort} onSort={(sort) => set({ sort })}>Decision</SortHeader>
                <TH>Owner</TH>
                <SortHeader field="next_action_date" sort={s.sort} onSort={(sort) => set({ sort })}>Next action</SortHeader>
              </TR></THead>
              <TBody>
                {data?.items.map((o) => (
                  <TR key={o.id} className="cursor-pointer" onClick={() => setEditing(o)}>
                    <TD><Link href={`/leads/${o.lead_id}`} onClick={(e) => e.stopPropagation()} className="font-medium hover:underline">{o.institution.name}</Link> <DemoBadge show={o.is_demo} /><div className="text-xs text-muted-foreground">{o.name}</div></TD>
                    <TD><StageBadge stage={o.stage} /></TD>
                    <TD className="text-right font-medium tabular">{money(o.estimated_value, o.currency)}</TD>
                    <TD className="text-muted-foreground">{o.solution_interest.map(label).join(", ") || "—"}</TD>
                    <TD>{date(o.expected_decision_date)}</TD>
                    <TD className="text-muted-foreground">{o.owner?.full_name ?? "—"}</TD>
                    <TD><div>{o.next_action ?? "—"}</div><div className="text-xs text-muted-foreground">{date(o.next_action_date)}</div></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {data && data.total === 0 && <EmptyState title="No opportunities" description="Open a lead and add an opportunity." />}
            {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => set({ page: String(p) })} />}
          </Card>
        </TabsContent>
      </Tabs>
      <OpportunityDialog open={!!editing} onOpenChange={(o) => !o && setEditing(undefined)} initial={editing} onSaved={() => mutate()} />
    </>
  );
}
