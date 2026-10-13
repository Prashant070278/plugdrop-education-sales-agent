"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Download, Plus, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import {
  DemoBadge, EmptyState, ErrorState, FilterSelect, FreshnessBadge, PageHeader, Pagination, SearchInput, SortHeader,
  StageBadge, SuppressedBadge, useListState,
} from "@/components/common";
import { ImportDialog, InstitutionDialog } from "@/components/forms";
import { qs, useApi } from "@/lib/api";
import { ResearchButton, ScorePill } from "@/components/research";
import { label } from "@/lib/format";
import { INSTITUTION_TYPES, STUDENT_SIZES, type Institution, type Page } from "@/lib/types";

export default function InstitutionsPage() {
  const router = useRouter();
  const [s, set] = useListState({ sort: "name", page: "1" });
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const { data: countries } = useApi<string[]>("/institutions/countries");
  const params = { q: s.q, sort: s.sort, page: s.page, page_size: 25, institution_type: s.type, country: s.country, student_size_range: s.size, demo: s.demo, tier: s.tier };
  const { data, error, mutate, isLoading } = useApi<Page<Institution>>("/institutions", params);
  const exportHref = `/api/institutions/export.csv${qs({ ...params, page: undefined, page_size: undefined })}`;

  return (
    <>
      <PageHeader eyebrow="Database" title="Institutions" description="Universities, colleges, EdTech and education providers we sell to."
        actions={<>
          <Button variant="outline" asChild><a href={exportHref}><Download />Export CSV</a></Button>
          <Button variant="outline" onClick={() => setImporting(true)}><Upload />Import CSV</Button>
          <Button onClick={() => setCreating(true)}><Plus />New institution</Button>
        </>} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput value={s.q ?? ""} onChange={(q) => set({ q })} placeholder="Search name, city, domain…" />
        <FilterSelect ariaLabel="Type" allLabel="All types" value={s.type ?? ""} onChange={(type) => set({ type })} options={INSTITUTION_TYPES} />
        <FilterSelect ariaLabel="Country" allLabel="All countries" value={s.country ?? ""} onChange={(country) => set({ country })} options={(countries ?? []).map((c) => ({ value: c, label: c }))} />
        <FilterSelect ariaLabel="Student size" allLabel="Any size" value={s.size ?? ""} onChange={(size) => set({ size })} options={STUDENT_SIZES} />
        <FilterSelect ariaLabel="Research tier" allLabel="Any research tier" value={s.tier ?? ""} onChange={(tier) => set({ tier })}
          options={[...["A", "B", "C", "D"].map((t) => ({ value: t, label: `Tier ${t}` })), { value: "none", label: "Not researched" }]} />
        <FilterSelect ariaLabel="Data" allLabel="Demo + real" value={s.demo ?? ""} onChange={(demo) => set({ demo })} options={[{ value: "false", label: "Real data only" }, { value: "true", label: "Demo only" }]} />
      </div>
      {selected.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-pdpurple/30 bg-pdpurple-bg/60 px-3 py-2 text-sm">
          <span className="font-medium">{selected.length} selected</span>
          <ResearchButton ids={selected} size="sm" label={`Research selected (${selected.length})`}
            onQueued={() => { setSelected([]); router.push("/research?tab=jobs"); }} />
          <Button size="sm" variant="ghost" onClick={() => setSelected([])}>Clear</Button>
        </div>
      )}
      {error && <ErrorState error={error} retry={() => mutate()} />}
      <Card>
        <Table>
          <THead>
            <TR className="hover:bg-transparent">
              <TH className="w-10">
                <input type="checkbox" aria-label="Select all on this page" className="accent-[#6D28D9]"
                  checked={!!data?.items.length && data.items.every((i) => selected.includes(i.id))}
                  onChange={(e) => setSelected(e.target.checked ? Array.from(new Set([...selected, ...(data?.items.map((i) => i.id) ?? [])])) : selected.filter((id) => !data?.items.some((i) => i.id === id)))} />
              </TH>
              <SortHeader field="name" sort={s.sort} onSort={(sort) => set({ sort })}>Institution</SortHeader>
              <SortHeader field="institution_type" sort={s.sort} onSort={(sort) => set({ sort })}>Type</SortHeader>
              <SortHeader field="country" sort={s.sort} onSort={(sort) => set({ sort })}>Location</SortHeader>
              <TH>Students</TH>
              <TH className="text-right">Contacts</TH>
              <TH>Lead stage</TH>
              <SortHeader field="research_score" sort={s.sort} onSort={(sort) => set({ sort })}>Research</SortHeader>
              <SortHeader field="last_verified_at" sort={s.sort} onSort={(sort) => set({ sort })}>Data</SortHeader>
              <TH>Owner</TH>
            </TR>
          </THead>
          <TBody>
            {isLoading && !data && Array.from({ length: 6 }).map((_, i) => <TR key={i}><TD colSpan={10}><Skeleton className="h-5" /></TD></TR>)}
            {data?.items.map((i) => (
              <TR key={i.id} className="cursor-pointer" onClick={() => router.push(`/institutions/${i.id}`)}>
                <TD onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" aria-label={`Select ${i.name}`} className="accent-[#6D28D9]" checked={selected.includes(i.id)}
                    onChange={(e) => setSelected(e.target.checked ? [...selected, i.id] : selected.filter((x) => x !== i.id))} />
                </TD>
                <TD>
                  <Link href={`/institutions/${i.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>{i.name}</Link>
                  <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">{i.domain ?? "No website"} <DemoBadge show={i.is_demo} /> <SuppressedBadge show={i.suppressed} /></div>
                </TD>
                <TD>{label(i.institution_type)}</TD>
                <TD>{[i.city, i.country].filter(Boolean).join(", ") || "—"}</TD>
                <TD>{label(i.student_size_range)}</TD>
                <TD className="text-right tabular">{i.contact_count}</TD>
                <TD><StageBadge stage={i.lead_stage} /></TD>
                <TD><ScorePill score={i.research_score} tier={i.research_tier} /></TD>
                <TD><FreshnessBadge value={i.freshness} /></TD>
                <TD className="text-muted-foreground">{i.owner?.full_name ?? "—"}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {data && data.total === 0 && <EmptyState title="No institutions found" description="Try a different search or filter, or add an institution." />}
        {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => set({ page: String(p) })} />}
      </Card>
      <InstitutionDialog open={creating} onOpenChange={setCreating} onSaved={(i) => router.push(`/institutions/${i.id}`)} />
      <ImportDialog open={importing} onOpenChange={setImporting} entity="institutions" onDone={() => mutate()} />
    </>
  );
}
