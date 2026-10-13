"use client";
import Link from "next/link";
import { use, useState } from "react";
import { useRouter } from "next/navigation";
import { ExternalLink, MapPin, Pencil, Plus, Trash2, UserPlus, Microscope, ShieldOff } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  DemoBadge, EmptyState, ErrorState, FreshnessBadge, PageHeader, StageBadge, SuppressedBadge, VerificationBadge,
} from "@/components/common";
import { ConsentDialog, ContactDialog, InstitutionDialog, LeadDialog, ResearchDialog } from "@/components/forms";
import { AuditList, Timeline } from "@/components/timeline";
import { useCan } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { date, label } from "@/lib/format";
import type { Activity, Contact, InstitutionDetail, Lead, Page, Research } from "@/lib/types";

export default function InstitutionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const toast = useToast();
  const canDelete = useCan("manager");
  const { data: inst, error, mutate } = useApi<InstitutionDetail>(`/institutions/${id}`);
  const { data: contacts, mutate: mutContacts } = useApi<Page<Contact>>("/contacts", { institution_id: id, page_size: 200 });
  const { data: leads, mutate: mutLeads } = useApi<Page<Lead>>("/leads", { institution_id: id, page_size: 50 });
  const { data: activity, mutate: mutAct } = useApi<Page<Activity>>("/activities", { institution_id: id, page_size: 100 });
  const { data: research, mutate: mutResearch } = useApi<Research[]>("/research", { institution_id: id });
  const [editing, setEditing] = useState(false);
  const [addContact, setAddContact] = useState(false);
  const [editContact, setEditContact] = useState<Contact | undefined>();
  const [optOut, setOptOut] = useState<Contact | undefined>();
  const [newLead, setNewLead] = useState(false);
  const [addResearch, setAddResearch] = useState(false);
  const [campus, setCampus] = useState("");

  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  if (!inst) return <div className="grid gap-4"><Skeleton className="h-16" /><Skeleton className="h-64" /></div>;

  const openLead = leads?.items.find((l) => l.stage !== "won" && l.stage !== "lost");

  const addCampus = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!campus.trim()) return;
    try {
      await api(`/institutions/${inst.id}/campuses`, { method: "POST", json: { name: campus.trim(), city: inst.city, country: inst.country } });
      setCampus("");
      mutate();
    } catch (err) { toast("error", err instanceof Error ? err.message : "Could not add campus"); }
  };
  const removeCampus = async (cid: number) => {
    await api(`/institutions/${inst.id}/campuses/${cid}`, { method: "DELETE" }).catch((e) => toast("error", e.message));
    mutate();
  };
  const remove = async () => {
    if (!confirm(`Delete ${inst.name}? This cannot be undone.`)) return;
    try {
      await api(`/institutions/${inst.id}`, { method: "DELETE" });
      toast("success", "Institution deleted");
      router.replace("/institutions");
    } catch (err) { toast("error", err instanceof Error ? err.message : "Delete failed"); }
  };

  return (
    <>
      <div className="mb-2 text-sm"><Link href="/institutions" className="text-muted-foreground hover:underline">Institutions</Link></div>
      <PageHeader eyebrow={label(inst.institution_type)} title={inst.name}
        actions={<>
          {openLead
            ? <Button variant="secondary" asChild><Link href={`/leads/${openLead.id}`}>Open lead · {label(openLead.stage)}</Link></Button>
            : <Button variant="secondary" onClick={() => setNewLead(true)}><Plus />Create lead</Button>}
          <Button variant="outline" onClick={() => setEditing(true)}><Pencil />Edit</Button>
          {canDelete && <Button variant="ghost" onClick={remove} aria-label="Delete institution"><Trash2 /></Button>}
        </>} />
      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <DemoBadge show={inst.is_demo} /><SuppressedBadge show={inst.suppressed} /><FreshnessBadge value={inst.freshness} />
        {(inst.city || inst.country) && <span className="inline-flex items-center gap-1"><MapPin className="size-3.5" />{[inst.city, inst.country].filter(Boolean).join(", ")}</span>}
        {inst.website && <a href={inst.website} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">{inst.domain}<ExternalLink className="size-3" /></a>}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="grid min-w-0 grid-cols-1 content-start gap-6 xl:col-span-2">
          <Card>
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <CardTitle>Contacts ({contacts?.total ?? 0})</CardTitle>
              <Button size="sm" onClick={() => { setEditContact(undefined); setAddContact(true); }}><UserPlus />Add contact</Button>
            </CardHeader>
            {contacts && contacts.total === 0 ? <EmptyState title="No contacts yet" description="Add the admissions, IT or leadership people you work with." /> : (
              <Table>
                <THead><TR className="hover:bg-transparent"><TH>Name</TH><TH>Role</TH><TH>Email</TH><TH>Status</TH><TH /></TR></THead>
                <TBody>
                  {contacts?.items.map((c) => (
                    <TR key={c.id}>
                      <TD><div className="font-medium">{c.full_name}</div><div className="flex gap-1">{c.linkedin_url && <a className="text-xs text-primary hover:underline" href={c.linkedin_url} target="_blank" rel="noopener noreferrer">LinkedIn</a>}<SuppressedBadge show={c.suppressed} /></div></TD>
                      <TD><div>{c.designation ?? "—"}</div><div className="text-xs text-muted-foreground">{c.department}</div></TD>
                      <TD className="text-muted-foreground">{c.email ?? "—"}</TD>
                      <TD><div className="flex flex-wrap gap-1"><VerificationBadge value={c.verification_status} /><FreshnessBadge value={c.freshness} /></div></TD>
                      <TD className="text-right whitespace-nowrap">
                        <Button size="icon" variant="ghost" aria-label={`Edit ${c.full_name}`} onClick={() => { setEditContact(c); setAddContact(true); }}><Pencil /></Button>
                        {!c.suppressed && <Button size="icon" variant="ghost" aria-label={`Opt out ${c.full_name}`} title="Record opt-out" onClick={() => setOptOut(c)}><ShieldOff /></Button>}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Card>

          <Card>
            <Tabs defaultValue="timeline">
              <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
                <TabsList><TabsTrigger value="timeline">Activity</TabsTrigger><TabsTrigger value="research">Research ({research?.length ?? 0})</TabsTrigger><TabsTrigger value="history">Audit</TabsTrigger></TabsList>
                <Button size="sm" variant="outline" onClick={() => setAddResearch(true)}><Microscope />Add finding</Button>
              </CardHeader>
              <CardContent>
                <TabsContent value="timeline" className="mt-0"><Timeline items={activity?.items} /></TabsContent>
                <TabsContent value="research" className="mt-0">
                  {research?.length === 0 && <p className="text-sm text-muted-foreground">No research findings yet.</p>}
                  <ul className="grid gap-3">
                    {research?.map((r) => (
                      <li key={r.id} className="rounded-lg border p-3 text-sm">
                        <div className="mb-1 flex items-center justify-between gap-2"><Badge variant="secondary">{r.category}</Badge><span className="text-xs text-muted-foreground">{date(r.found_at)} · confidence {r.confidence}%</span></div>
                        <p>{r.summary}</p>
                        {r.source_url && <a href={r.source_url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-xs text-primary hover:underline">Source</a>}
                      </li>
                    ))}
                  </ul>
                </TabsContent>
                <TabsContent value="history" className="mt-0"><AuditList entity="institution" id={inst.id} /></TabsContent>
              </CardContent>
            </Tabs>
          </Card>
        </div>

        <div className="grid min-w-0 grid-cols-1 content-start gap-6">
          <Card>
            <CardHeader><CardTitle>Profile</CardTitle></CardHeader>
            <CardContent>
              <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2.5 text-sm">
                <dt className="text-muted-foreground">Students</dt><dd>{label(inst.student_size_range)}</dd>
                <dt className="text-muted-foreground">Owner</dt><dd>{inst.owner?.full_name ?? "—"}</dd>
                <dt className="text-muted-foreground">Source</dt><dd>{inst.source ?? "—"}</dd>
                <dt className="text-muted-foreground">Last verified</dt><dd>{date(inst.last_verified_at)}</dd>
                <dt className="text-muted-foreground">Added</dt><dd>{date(inst.created_at)}</dd>
                <dt className="text-muted-foreground">Courses</dt>
                <dd className="flex flex-wrap gap-1">{inst.courses.length ? inst.courses.map((c) => <Badge key={c} variant="muted">{c}</Badge>) : "—"}</dd>
              </dl>
              {inst.notes && <p className="mt-4 whitespace-pre-wrap border-t pt-3 text-sm text-muted-foreground">{inst.notes}</p>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Campuses ({inst.campuses.length})</CardTitle></CardHeader>
            <CardContent className="grid gap-2">
              {inst.campuses.map((c) => (
                <div key={c.id} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                  <span>{c.name}<span className="ml-2 text-xs text-muted-foreground">{[c.city, c.country].filter(Boolean).join(", ")}</span></span>
                  <button aria-label={`Remove ${c.name}`} onClick={() => removeCampus(c.id)} className="text-muted-foreground hover:text-destructive"><Trash2 className="size-3.5" /></button>
                </div>
              ))}
              <form onSubmit={addCampus} className="flex gap-2">
                <Input aria-label="New campus name" placeholder="Add campus…" value={campus} onChange={(e) => setCampus(e.target.value)} />
                <Button type="submit" variant="outline" size="icon" aria-label="Add campus"><Plus /></Button>
              </form>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Leads</CardTitle></CardHeader>
            <CardContent className="grid gap-2">
              {leads?.items.length === 0 && <p className="text-sm text-muted-foreground">No leads yet.</p>}
              {leads?.items.map((l) => (
                <Link key={l.id} href={`/leads/${l.id}`} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm hover:bg-muted">
                  <span>Lead #{l.id} · {l.owner?.full_name ?? "Unassigned"}</span><StageBadge stage={l.stage} />
                </Link>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>

      <InstitutionDialog open={editing} onOpenChange={setEditing} initial={inst} onSaved={() => mutate()} />
      <ContactDialog open={addContact} onOpenChange={setAddContact} initial={editContact} institution={{ id: inst.id, name: inst.name }} onSaved={() => { mutContacts(); mutate(); }} />
      <ConsentDialog open={!!optOut} onOpenChange={(o) => !o && setOptOut(undefined)} contact={optOut} onSaved={() => { mutContacts(); mutate(); }} />
      <LeadDialog open={newLead} onOpenChange={setNewLead} institution={{ id: inst.id, name: inst.name }} onSaved={(l) => { mutLeads(); router.push(`/leads/${l.id}`); }} />
      <ResearchDialog open={addResearch} onOpenChange={setAddResearch} institutionId={inst.id} leadId={openLead?.id} onSaved={() => { mutResearch(); mutAct(); }} />
    </>
  );
}
