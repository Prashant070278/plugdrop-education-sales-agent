"use client";
import Link from "next/link";
import { use, useState } from "react";
import { ArrowRightLeft, Building2, CalendarClock, Check, MailPlus, Microscope, Pencil, Plus, MessageSquarePlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DemoBadge, ErrorState, PageHeader, PriorityBadge, StageBadge } from "@/components/common";
import { ActivityDialog, OpportunityDialog, OwnerSelect, ResearchDialog, StageDialog, TaskDialog } from "@/components/forms";
import { AuditList, Timeline } from "@/components/timeline";
import { SequenceStatusBadge, StartOutreachDialog } from "@/components/outreach";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { date, dateTime, label, money } from "@/lib/format";
import { LEAD_STAGES, type Activity, type Contact, type Lead, type Opportunity, type OutreachSequence, type Page, type Research, type Task } from "@/lib/types";
import { cn } from "@/lib/utils";

function StageTrack({ stage }: { stage: Lead["stage"] }) {
  const flow = LEAD_STAGES.filter((s) => !["lost", "nurture"].includes(s));
  const idx = flow.indexOf(stage as (typeof flow)[number]);
  return (
    <ol className="flex flex-wrap gap-1" aria-label="Pipeline progress">
      {flow.map((s, i) => (
        <li key={s} aria-current={s === stage ? "step" : undefined}
          className={cn("rounded px-2 py-1 text-[11px] font-medium",
            s === stage ? "bg-primary text-primary-foreground" : i < idx ? "bg-pdpurple-bg text-pdpurple-deep" : "bg-muted text-muted-foreground")}>
          {label(s)}
        </li>
      ))}
      {(stage === "lost" || stage === "nurture") && <li className="ml-1"><StageBadge stage={stage} /></li>}
    </ol>
  );
}

export default function LeadDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const toast = useToast();
  const { data: lead, error, mutate } = useApi<Lead>(`/leads/${id}`);
  const { data: timeline, mutate: mutTl } = useApi<Activity[]>(`/leads/${id}/timeline`);
  const { data: tasks, mutate: mutTasks } = useApi<Task[]>(`/leads/${id}/tasks`);
  const { data: opps, mutate: mutOpps } = useApi<Page<Opportunity>>("/opportunities", { lead_id: id, page_size: 50 });
  const { data: research, mutate: mutResearch } = useApi<Research[]>(`/leads/${id}/research`);
  const { data: contacts } = useApi<Page<Contact>>(lead ? "/contacts" : null, { institution_id: lead?.institution.id, page_size: 100 });
  const { data: seqs } = useApi<Page<OutreachSequence>>("/outreach/sequences", { lead_id: id, page_size: 20 });
  const [dialog, setDialog] = useState<null | "stage" | "activity" | "task" | "opp" | "research" | "outreach">(null);
  const [editOpp, setEditOpp] = useState<Opportunity | undefined>();
  const [editTask, setEditTask] = useState<Task | undefined>();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ primary_contact_id: "", owner_id: null as number | null, score: "", summary: "" });

  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  if (!lead) return <div className="grid gap-4"><Skeleton className="h-16" /><Skeleton className="h-96" /></div>;

  const refreshAll = () => { mutate(); mutTl(); mutTasks(); mutOpps(); mutResearch(); };
  const toggleTask = async (t: Task) => {
    await api(`/tasks/${t.id}`, { method: "PATCH", json: { status: t.status === "open" ? "done" : "open" } }).catch((e) => toast("error", e.message));
    mutTasks(); mutate();
  };
  const startEdit = () => {
    setDraft({ primary_contact_id: lead.primary_contact ? String(lead.primary_contact.id) : "", owner_id: lead.owner?.id ?? null, score: String(lead.score), summary: lead.summary ?? "" });
    setEditing(true);
  };
  const saveEdit = async () => {
    try {
      await api(`/leads/${lead.id}`, { method: "PATCH", json: { primary_contact_id: draft.primary_contact_id ? Number(draft.primary_contact_id) : null, owner_id: draft.owner_id, score: Number(draft.score), summary: draft.summary || null } });
      toast("success", "Lead updated");
      setEditing(false);
      refreshAll();
    } catch (e) { toast("error", e instanceof Error ? e.message : "Update failed"); }
  };

  return (
    <>
      <div className="mb-2 text-sm"><Link href="/leads" className="text-muted-foreground hover:underline">Leads</Link></div>
      <PageHeader eyebrow={`Lead #${lead.id}`} title={lead.institution.name}
        actions={<>
          <Button variant="outline" onClick={() => setDialog("outreach")}><MailPlus />Start outreach</Button>
          <Button variant="outline" onClick={() => setDialog("activity")}><MessageSquarePlus />Log activity</Button>
          <Button variant="outline" onClick={() => { setEditTask(undefined); setDialog("task"); }}><CalendarClock />Add task</Button>
          <Button onClick={() => setDialog("stage")}><ArrowRightLeft />Move stage</Button>
        </>} />
      <div className="mb-6 grid gap-3">
        <StageTrack stage={lead.stage} />
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <DemoBadge show={lead.is_demo} />
          <Link href={`/institutions/${lead.institution.id}`} className="inline-flex items-center gap-1 text-primary hover:underline"><Building2 className="size-3.5" />Institution profile</Link>
          <span>· In {label(lead.stage)} since {date(lead.stage_changed_at)}</span>
          {lead.lost_reason && <span>· Lost: {lead.lost_reason}</span>}
        </div>
        {!!seqs?.items.length && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">Outreach:</span>
            {seqs.items.map((q) => (
              <Link key={q.id} href={`/outreach/sequences/${q.id}`} className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 hover:bg-muted">
                {q.contact_name} <SequenceStatusBadge status={q.status} reason={q.stop_reason} />
              </Link>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card className="min-w-0 xl:col-span-2">
          <Tabs defaultValue="timeline">
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <TabsList><TabsTrigger value="timeline">Timeline</TabsTrigger><TabsTrigger value="research">Research ({research?.length ?? 0})</TabsTrigger><TabsTrigger value="audit">Audit</TabsTrigger></TabsList>
              <Button size="sm" variant="ghost" onClick={() => setDialog("research")}><Microscope />Add finding</Button>
            </CardHeader>
            <CardContent>
              <TabsContent value="timeline" className="mt-0"><Timeline items={timeline} /></TabsContent>
              <TabsContent value="research" className="mt-0">
                {research?.length === 0 && <p className="text-sm text-muted-foreground">No research yet.</p>}
                <ul className="grid gap-3">{research?.map((r) => (
                  <li key={r.id} className="rounded-lg border p-3 text-sm">
                    <div className="mb-1 flex justify-between text-xs text-muted-foreground"><span className="font-medium text-foreground">{r.category}</span><span>{date(r.found_at)} · {r.confidence}% confidence</span></div>
                    <p>{r.summary}</p>
                    {r.source_url && <a href={r.source_url} target="_blank" rel="noopener noreferrer" className="text-xs text-primary hover:underline">Source</a>}
                  </li>
                ))}</ul>
              </TabsContent>
              <TabsContent value="audit" className="mt-0"><AuditList entity="lead" id={lead.id} /></TabsContent>
            </CardContent>
          </Tabs>
        </Card>

        <div className="grid min-w-0 grid-cols-1 content-start gap-6">
          <Card>
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <CardTitle>Details</CardTitle>
              {!editing && <Button size="sm" variant="ghost" onClick={startEdit}><Pencil />Edit</Button>}
            </CardHeader>
            <CardContent>
              {editing ? (
                <div className="grid gap-3 text-sm">
                  <label className="grid gap-1"><span className="font-medium">Primary contact</span>
                    <Select value={draft.primary_contact_id} onChange={(e) => setDraft({ ...draft, primary_contact_id: e.target.value })}>
                      <option value="">— None —</option>
                      {contacts?.items.map((c) => <option key={c.id} value={c.id}>{c.full_name}{c.suppressed ? " (do not contact)" : ""}</option>)}
                    </Select></label>
                  <label className="grid gap-1"><span className="font-medium">Owner</span><OwnerSelect id="ld-owner" value={draft.owner_id} onChange={(v) => setDraft({ ...draft, owner_id: v })} /></label>
                  <label className="grid gap-1"><span className="font-medium">Score</span><Input type="number" min={0} max={100} value={draft.score} onChange={(e) => setDraft({ ...draft, score: e.target.value })} /></label>
                  <label className="grid gap-1"><span className="font-medium">Summary</span><textarea className="min-h-20 rounded-md border px-3 py-2" value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} /></label>
                  <div className="flex justify-end gap-2"><Button variant="outline" size="sm" onClick={() => setEditing(false)}>Cancel</Button><Button size="sm" onClick={saveEdit}>Save</Button></div>
                </div>
              ) : (
                <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2.5 text-sm">
                  <dt className="text-muted-foreground">Contact</dt><dd>{lead.primary_contact ? <>{lead.primary_contact.full_name}<div className="text-xs text-muted-foreground">{lead.primary_contact.designation} · {lead.primary_contact.email}</div></> : "—"}</dd>
                  <dt className="text-muted-foreground">Owner</dt><dd>{lead.owner?.full_name ?? "—"}</dd>
                  <dt className="text-muted-foreground">Score</dt><dd className="tabular">{lead.score} / 100</dd>
                  <dt className="text-muted-foreground">Source</dt><dd>{lead.source ?? "—"}</dd>
                  <dt className="text-muted-foreground">Summary</dt><dd className="whitespace-pre-wrap">{lead.summary ?? "—"}</dd>
                </dl>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <CardTitle>Opportunities</CardTitle>
              <Button size="sm" variant="ghost" onClick={() => { setEditOpp(undefined); setDialog("opp"); }}><Plus />Add</Button>
            </CardHeader>
            <CardContent className="grid gap-2">
              {opps?.items.length === 0 && <p className="text-sm text-muted-foreground">No opportunity yet.</p>}
              {opps?.items.map((o) => (
                <button key={o.id} onClick={() => { setEditOpp(o); setDialog("opp"); }} className="rounded-md border p-3 text-left text-sm hover:bg-muted">
                  <div className="flex items-center justify-between gap-2"><span className="font-medium">{money(o.estimated_value, o.currency)}</span><StageBadge stage={o.stage} /></div>
                  <div className="mt-1 text-xs text-muted-foreground">{o.solution_interest.map(label).join(", ") || "No solution set"} · decision {date(o.expected_decision_date)}</div>
                  {o.next_action && <div className="mt-1 text-xs">Next: {o.next_action} {o.next_action_date && `(${date(o.next_action_date)})`}</div>}
                </button>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Tasks</CardTitle></CardHeader>
            <CardContent className="grid gap-2">
              {tasks?.length === 0 && <p className="text-sm text-muted-foreground">No tasks.</p>}
              {tasks?.map((t) => (
                <div key={t.id} className="flex items-start gap-2.5 text-sm">
                  <button onClick={() => toggleTask(t)} aria-label={t.status === "open" ? `Complete ${t.title}` : `Reopen ${t.title}`}
                    className={cn("mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border", t.status === "done" && "border-primary bg-primary text-primary-foreground")}>
                    {t.status === "done" && <Check className="size-3" />}
                  </button>
                  <button className="min-w-0 flex-1 text-left" onClick={() => { setEditTask(t); setDialog("task"); }}>
                    <div className={cn(t.status === "done" && "text-muted-foreground line-through")}>{t.title}</div>
                    <div className={cn("text-xs", t.overdue ? "text-red-700" : "text-muted-foreground")}>{t.due_at ? `Due ${dateTime(t.due_at)}` : "No due date"} · {t.owner?.full_name}</div>
                  </button>
                  <PriorityBadge value={t.priority} />
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>

      <StageDialog open={dialog === "stage"} onOpenChange={(o) => !o && setDialog(null)} lead={lead} onSaved={refreshAll} />
      <ActivityDialog open={dialog === "activity"} onOpenChange={(o) => !o && setDialog(null)} leadId={lead.id} institutionId={lead.institution.id} onSaved={refreshAll} />
      <TaskDialog open={dialog === "task"} onOpenChange={(o) => !o && setDialog(null)} initial={editTask} link={{ lead_id: lead.id, institution_id: lead.institution.id, contact_id: lead.primary_contact?.id }} onSaved={refreshAll} />
      <OpportunityDialog open={dialog === "opp"} onOpenChange={(o) => !o && setDialog(null)} lead={lead} initial={editOpp} onSaved={refreshAll} />
      <ResearchDialog open={dialog === "research"} onOpenChange={(o) => !o && setDialog(null)} institutionId={lead.institution.id} leadId={lead.id} onSaved={refreshAll} />
      <StartOutreachDialog open={dialog === "outreach"} onOpenChange={(o) => setDialog(o ? "outreach" : null)} leadId={lead.id} />
    </>
  );
}
