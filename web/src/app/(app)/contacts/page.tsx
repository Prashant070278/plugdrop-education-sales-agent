"use client";
import Link from "next/link";
import { useState } from "react";
import { Download, Pencil, Plus, ShieldOff, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import {
  DemoBadge, EmptyState, ErrorState, FilterSelect, FreshnessBadge, PageHeader, Pagination, SearchInput, SortHeader,
  SuppressedBadge, VerificationBadge, useListState,
} from "@/components/common";
import { ConsentDialog, ContactDialog, ImportDialog } from "@/components/forms";
import { qs, useApi } from "@/lib/api";
import { VERIFICATION, type Contact, type Page } from "@/lib/types";

export default function ContactsPage() {
  const [s, set] = useListState({ sort: "last_name", page: "1" });
  const [editing, setEditing] = useState<Contact | undefined>();
  const [open, setOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [optOut, setOptOut] = useState<Contact | undefined>();
  const params = { q: s.q, sort: s.sort, page: s.page, page_size: 25, verification_status: s.status, department: s.dept, demo: s.demo };
  const { data, error, mutate, isLoading } = useApi<Page<Contact>>("/contacts", params);
  const exportHref = `/api/contacts/export.csv${qs({ ...params, page: undefined, page_size: undefined })}`;

  return (
    <>
      <PageHeader eyebrow="Database" title="Contacts" description="Decision-makers and influencers at our target institutions."
        actions={<>
          <Button variant="outline" asChild><a href={exportHref}><Download />Export CSV</a></Button>
          <Button variant="outline" onClick={() => setImporting(true)}><Upload />Import CSV</Button>
          <Button onClick={() => { setEditing(undefined); setOpen(true); }}><Plus />New contact</Button>
        </>} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput value={s.q ?? ""} onChange={(q) => set({ q })} placeholder="Search name, email, title, institution…" />
        <FilterSelect ariaLabel="Verification" allLabel="Any verification" value={s.status ?? ""} onChange={(status) => set({ status })} options={VERIFICATION} />
        <FilterSelect ariaLabel="Department" allLabel="Any department" value={s.dept ?? ""} onChange={(dept) => set({ dept })}
          options={["Admissions", "IT", "Marketing", "Student Services", "Leadership", "Academics", "Registrar"].map((d) => ({ value: d, label: d }))} />
        <FilterSelect ariaLabel="Data" allLabel="Demo + real" value={s.demo ?? ""} onChange={(demo) => set({ demo })} options={[{ value: "false", label: "Real data only" }, { value: "true", label: "Demo only" }]} />
      </div>
      {error && <ErrorState error={error} retry={() => mutate()} />}
      <Card>
        <Table>
          <THead>
            <TR className="hover:bg-transparent">
              <SortHeader field="last_name" sort={s.sort} onSort={(sort) => set({ sort })}>Name</SortHeader>
              <SortHeader field="designation" sort={s.sort} onSort={(sort) => set({ sort })}>Designation</SortHeader>
              <SortHeader field="institution" sort={s.sort} onSort={(sort) => set({ sort })}>Institution</SortHeader>
              <TH>Email / phone</TH>
              <SortHeader field="verification_status" sort={s.sort} onSort={(sort) => set({ sort })}>Verification</SortHeader>
              <SortHeader field="last_verified_at" sort={s.sort} onSort={(sort) => set({ sort })}>Data</SortHeader>
              <TH />
            </TR>
          </THead>
          <TBody>
            {isLoading && !data && Array.from({ length: 6 }).map((_, i) => <TR key={i}><TD colSpan={7}><Skeleton className="h-5" /></TD></TR>)}
            {data?.items.map((c) => (
              <TR key={c.id}>
                <TD><div className="font-medium">{c.full_name}</div><div className="mt-0.5 flex gap-1"><DemoBadge show={c.is_demo} /><SuppressedBadge show={c.suppressed} /></div></TD>
                <TD><div>{c.designation ?? "—"}</div><div className="text-xs text-muted-foreground">{c.department}</div></TD>
                <TD><Link href={`/institutions/${c.institution.id}`} className="hover:underline">{c.institution.name}</Link></TD>
                <TD className="text-muted-foreground"><div>{c.email ?? "—"}</div><div className="text-xs">{c.phone}</div></TD>
                <TD><VerificationBadge value={c.verification_status} /></TD>
                <TD><FreshnessBadge value={c.freshness} /></TD>
                <TD className="whitespace-nowrap text-right">
                  <Button size="icon" variant="ghost" aria-label={`Edit ${c.full_name}`} onClick={() => { setEditing(c); setOpen(true); }}><Pencil /></Button>
                  {!c.suppressed && <Button size="icon" variant="ghost" aria-label={`Opt out ${c.full_name}`} title="Record opt-out" onClick={() => setOptOut(c)}><ShieldOff /></Button>}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {data && data.total === 0 && <EmptyState title="No contacts found" description="Adjust the filters or add a contact." />}
        {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => set({ page: String(p) })} />}
      </Card>
      <ContactDialog open={open} onOpenChange={setOpen} initial={editing} onSaved={() => mutate()} />
      <ConsentDialog open={!!optOut} onOpenChange={(o) => !o && setOptOut(undefined)} contact={optOut} onSaved={() => mutate()} />
      <ImportDialog open={importing} onOpenChange={setImporting} entity="contacts" onDone={() => mutate()} />
    </>
  );
}
