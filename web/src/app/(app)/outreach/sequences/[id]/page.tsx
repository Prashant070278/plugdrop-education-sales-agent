"use client";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { Ban, CalendarClock, Check, ExternalLink, Mail, MessageSquareReply, Pencil, RotateCcw, Send, Sparkles, Undo2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { DemoBadge, ErrorState, Field, PageHeader } from "@/components/common";
import { MessageStatusBadge, ModeNotice, QualityChecks, ReplyLabelBadge, SequenceStatusBadge, replyLabelText } from "@/components/outreach";
import { useCan } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, fromLocalInput, label, relative } from "@/lib/format";
import { REPLY_LABELS, type EmailReply, type OutreachMessage, type OutreachSequenceDetail, type ReplyLabel } from "@/lib/types";
import { cn } from "@/lib/utils";

const UNSENT = ["draft", "approved", "scheduled", "blocked"];
const LIVE = ["draft", "active"];

function host(u?: string | null) {
  try { return u ? new URL(u).hostname.replace(/^www\./, "") : ""; } catch { return u ?? ""; }
}

function MessageCard({ m, seq, act }: {
  m: OutreachMessage; seq: OutreachSequenceDetail;
  act: (path: string, init: RequestInit & { json?: unknown }, ok: string) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(m.subject);
  const [body, setBody] = useState(m.body);
  const [when, setWhen] = useState("");
  const [showWhy, setShowWhy] = useState(false);
  useEffect(() => { if (!editing) { setSubject(m.subject); setBody(m.body); } }, [m.subject, m.body, editing]);
  const open = UNSENT.includes(m.status);
  const stopped = m.kind === "sequence" && !LIVE.includes(seq.status);
  const title = m.kind === "reply" ? "Your reply" : m.step === 1 ? "First email" : `Follow-up ${m.step - 1}`;
  const offset = m.kind === "sequence" && m.step > 1 ? `${seq.spacing_days[m.step - 1]} days after the previous email` : null;
  const g = m.generation as { rationale?: string; facts_used?: string[]; content_used?: string[] };
  const base = `/outreach/messages/${m.id}`;
  const save = async () => { if (await act(base, { method: "PATCH", json: { subject, body } }, "Saved. Approve it again when you're happy.")) setEditing(false); };
  return (
    <Card id={`m${m.id}`} className={cn("scroll-mt-20", m.status === "cancelled" && "opacity-60")}>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2 space-y-0 pb-3">
        <div>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">{title} <MessageStatusBadge status={m.status} />{m.edited && <Badge variant="muted">Edited</Badge>}</CardTitle>
          <CardDescription className="mt-1">
            To {m.to_email}
            {m.sent_at ? ` · sent ${dateTime(m.sent_at)}` : m.scheduled_for && open ? ` · due ${dateTime(m.scheduled_for)} (${relative(m.scheduled_for)})` : offset ? ` · ${offset}` : ""}
            {m.delivered_at ? ` · delivered ${relative(m.delivered_at)}` : ""}
          </CardDescription>
        </div>
        {open && !stopped && !editing && <QualityChecks quality={m.quality} compact />}
      </CardHeader>
      <CardContent className="grid gap-3">
        {m.last_error && <p className={cn("rounded-md p-2.5 text-sm", m.status === "blocked" ? "bg-amber-50 text-amber-900" : "bg-red-50 text-red-800")}>{m.status === "blocked" ? "Held back: " : ""}{m.last_error}</p>}
        {editing ? (
          <>
            <Field label="Subject" htmlFor={`s${m.id}`}><Input id={`s${m.id}`} value={subject} onChange={(e) => setSubject(e.target.value)} /></Field>
            <Field label="Email" htmlFor={`b${m.id}`}><Textarea id={`b${m.id}`} rows={14} className="font-mono text-[13px]" value={body} onChange={(e) => setBody(e.target.value)} /></Field>
            <p className="text-xs text-muted-foreground">Keep the signature and the unsubscribe line. Saving re-runs the quality checks; an approved email goes back to draft.</p>
            <div className="flex flex-wrap gap-2"><Button size="sm" onClick={save}><Check />Save</Button><Button size="sm" variant="outline" onClick={() => setEditing(false)}>Discard changes</Button></div>
          </>
        ) : (
          <div className="rounded-lg border bg-muted/30 p-4">
            <div className="mb-2 text-sm font-semibold">{m.subject}</div>
            <div className="whitespace-pre-wrap text-sm leading-relaxed">{m.body}</div>
          </div>
        )}
        {open && !stopped && !editing && (
          <>
            <QualityChecks quality={m.quality} />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => setEditing(true)}><Pencil />Edit</Button>
              <Button size="sm" variant="outline" onClick={() => act(`${base}/regenerate`, { method: "POST" }, "Redrafted")}><Sparkles />Redraft</Button>
              {m.status === "draft" && <Button size="sm" onClick={() => act(`${base}/approve`, { method: "POST" }, "Approved")} disabled={!m.quality.passed} title={m.quality.passed ? undefined : "Fix the failed checks first"}><Check />Approve</Button>}
              {["approved", "blocked"].includes(m.status) && <Button size="sm" variant="outline" onClick={() => act(`${base}/schedule`, { method: "POST", json: { when: null } }, "Scheduled for the next sending window")}><CalendarClock />Schedule</Button>}
              {["approved", "scheduled", "blocked"].includes(m.status) && <Button size="sm" onClick={() => act(`${base}/send-now`, { method: "POST" }, seq.mailbox?.effective_mode === "send" ? "Sent" : "Placed in the mailbox as a draft")}><Send />{seq.mailbox?.effective_mode === "send" ? "Send now" : "Put in mailbox now"}</Button>}
              <Button size="sm" variant="ghost" onClick={() => act(`${base}/cancel`, { method: "POST" }, "Cancelled")}><X />Cancel email</Button>
            </div>
            {["approved", "scheduled", "blocked"].includes(m.status) && (
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Or schedule for" htmlFor={`w${m.id}`}><Input id={`w${m.id}`} type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} className="w-56" /></Field>
                <Button size="sm" variant="outline" disabled={!when} onClick={() => act(`${base}/schedule`, { method: "POST", json: { when: fromLocalInput(when) } }, "Scheduled")}>Set time</Button>
              </div>
            )}
          </>
        )}
        {stopped && open && <p className="text-xs text-muted-foreground">Follow-ups stopped — this email won&apos;t be sent.</p>}
        {(g.rationale || g.facts_used?.length) && (
          <div>
            <button type="button" className="text-xs text-primary hover:underline" onClick={() => setShowWhy(!showWhy)}>{showWhy ? "Hide" : "Why this email?"}</button>
            {showWhy && (
              <div className="mt-1 grid gap-1 text-xs text-muted-foreground">
                {g.rationale && <p>{g.rationale}</p>}
                {!!g.facts_used?.length && <p>Facts used: {g.facts_used.join(", ")} · Approved content: {g.content_used?.join(", ") || "none"} · Written by {m.generated_by === "claude" ? "Claude" : "rule-based templates"}</p>}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ReplyCard({ r, act, canDraft }: { r: EmailReply; act: (path: string, init: RequestInit & { json?: unknown }, ok: string) => Promise<boolean>; canDraft: boolean }) {
  return (
    <li id={`r${r.id}`} className={cn("scroll-mt-20 rounded-lg border p-3.5", !r.handled && "border-pdpurple/40 bg-pdpurple-bg/30")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5"><ReplyLabelBadge value={r.label} optOut={r.opt_out} />{r.handled && <Badge variant="muted">Handled</Badge>}</div>
        <span className="text-xs text-muted-foreground">{dateTime(r.received_at)}</span>
      </div>
      <div className="mt-2 text-sm font-medium">{r.subject}</div>
      <div className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-sm text-muted-foreground">{r.body}</div>
      {r.summary && <p className="mt-1.5 text-xs text-muted-foreground">{r.summary}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Select aria-label="Reply classification" className="h-8 w-48 text-xs" value={r.label}
          onChange={(e) => act(`/outreach/replies/${r.id}`, { method: "PATCH", json: { label: e.target.value } }, `Marked as ${replyLabelText(e.target.value as ReplyLabel)}`)}>
          {REPLY_LABELS.map((l) => <option key={l} value={l}>{replyLabelText(l)}</option>)}
        </Select>
        {canDraft && !r.opt_out && r.label !== "out_of_office" && <Button size="sm" variant="outline" onClick={() => act(`/outreach/replies/${r.id}/draft`, { method: "POST" }, "Reply drafted below — review it")}><MessageSquareReply />Draft a reply</Button>}
        {!r.opt_out && <Button size="sm" variant="ghost" onClick={() => { if (confirm("Record an opt-out for this person? They won't be emailed again.")) act(`/outreach/replies/${r.id}`, { method: "PATCH", json: { opt_out: true } }, "Opt-out recorded"); }}><Ban />Record opt-out</Button>}
        <Button size="sm" variant="ghost" onClick={() => act(`/outreach/replies/${r.id}`, { method: "PATCH", json: { handled: !r.handled } }, r.handled ? "Marked as needing attention" : "Marked handled")}>{r.handled ? <><Undo2 />Reopen</> : <><Check />Mark handled</>}</Button>
      </div>
    </li>
  );
}

export default function SequencePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const toast = useToast();
  const isManager = useCan("manager");
  const { data: seq, error, mutate } = useApi<OutreachSequenceDetail>(`/outreach/sequences/${id}`);
  const [sim, setSim] = useState("");
  useEffect(() => {
    if (!seq || !window.location.hash) return;
    document.getElementById(window.location.hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [seq?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  if (!seq) return <div className="grid gap-4"><Skeleton className="h-20" /><Skeleton className="h-96" /></div>;

  const act = async (path: string, init: RequestInit & { json?: unknown }, ok: string) => {
    try {
      const r = await api<OutreachSequenceDetail | EmailReply>(path, init);
      if (r && "messages" in r) mutate(r, { revalidate: false }); else mutate();
      toast("success", ok);
      return true;
    } catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); mutate(); return false; }
  };
  const stop = (reason: "not_a_fit" | "cancelled") => {
    const note = reason === "not_a_fit" ? prompt("Why is this prospect not a fit? (saved on the lead)") : null;
    if (reason === "not_a_fit" && note === null) return;
    if (reason === "cancelled" && !confirm("Stop this sequence? Unsent emails won't go out.")) return;
    act(`/outreach/sequences/${seq.id}/stop`, { method: "POST", json: { reason, note } }, "Sequence stopped");
  };
  const live = LIVE.includes(seq.status);
  const steps = seq.messages.filter((m) => m.kind === "sequence").sort((a, b) => a.step - b.step);
  const replyDrafts = seq.messages.filter((m) => m.kind === "reply").sort((a, b) => a.step - b.step);
  const sentAny = seq.messages.some((m) => ["sent", "delivered"].includes(m.status));
  const LEVEL: Record<string, [string, "success" | "secondary" | "muted"]> = { verified_fact: ["Verified", "success"], source_inference: ["Inference — worded carefully", "secondary"] };

  return (
    <>
      <div className="mb-2 text-sm"><Link href="/outreach" className="text-muted-foreground hover:underline">Outreach</Link></div>
      <PageHeader eyebrow={`Sequence #${seq.id}${seq.use_case_label ? ` · ${seq.use_case_label}` : ""}`} title={seq.institution.name}
        actions={live ? <>
          <Button variant="outline" onClick={() => stop("not_a_fit")}><Ban />Not a fit</Button>
          <Button variant="outline" onClick={() => stop("cancelled")}><X />Stop sequence</Button>
        </> : undefined} />
      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <DemoBadge show={seq.is_demo} />
        <SequenceStatusBadge status={seq.status} reason={seq.stop_reason} />
        <span>To <span className="font-medium text-foreground">{seq.contact_name}</span>{seq.contact_designation ? `, ${seq.contact_designation}` : ""} &lt;{seq.contact_email}&gt;</span>
        {seq.mailbox && <span className="inline-flex items-center gap-1.5">· <Mail className="size-3.5" />{seq.mailbox.email_address} <ModeNotice mode={seq.mailbox.effective_mode} reason={seq.mailbox.effective_mode_reason} /></span>}
        <Link href={`/leads/${seq.lead_id}`} className="text-primary hover:underline">Lead</Link>
      </div>
      {seq.contact_suppressed && <p className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-800">This contact is opted out or suppressed. Nothing more will be sent.</p>}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="grid min-w-0 content-start gap-4 xl:col-span-2">
          {steps.map((m) => <MessageCard key={m.id} m={m} seq={seq} act={act} />)}
          {replyDrafts.map((m) => <MessageCard key={m.id} m={m} seq={seq} act={act} />)}
        </div>

        <div className="grid min-w-0 content-start gap-6">
          <Card>
            <CardHeader><CardTitle>Replies ({seq.replies.length})</CardTitle><CardDescription className="mt-1">Any reply stops the remaining follow-ups. Opt-outs are recorded and respected everywhere.</CardDescription></CardHeader>
            <CardContent className="grid gap-3">
              {seq.replies.length === 0 ? <p className="text-sm text-muted-foreground">No replies yet.</p> : (
                <ul className="grid gap-2">{seq.replies.map((r) => <ReplyCard key={r.id} r={r} act={act} canDraft={!["opted_out", "suppressed", "bounced"].includes(seq.stop_reason ?? "")} />)}</ul>
              )}
              {isManager && seq.mailbox?.provider === "mock" && sentAny && (
                <div className="rounded-lg border border-dashed p-3">
                  <Field label="Test: simulate the prospect replying" htmlFor="sim" hint="Mock mailbox only. Try “Can we talk Thursday at 3pm?” or “Please unsubscribe me.”">
                    <Textarea id="sim" rows={3} value={sim} onChange={(e) => setSim(e.target.value)} />
                  </Field>
                  <Button size="sm" className="mt-2" variant="outline" disabled={!sim.trim()} onClick={async () => { if (await act("/outreach/mock/reply", { method: "POST", json: { sequence_id: seq.id, body: sim } }, "Reply received and classified")) setSim(""); }}><RotateCcw />Simulate reply</Button>
                </div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Facts the emails may use ({seq.facts.length})</CardTitle><CardDescription className="mt-1">Only approved research. Inferences must be worded as observations, not claims.</CardDescription></CardHeader>
            <CardContent>
              <ul className="grid gap-2.5 text-sm">
                {seq.facts.map((f) => (
                  <li key={f.id}>
                    <div className="flex flex-wrap items-center gap-1.5"><Badge variant="muted">{f.id}</Badge><Badge variant={LEVEL[f.level]?.[1] ?? "muted"}>{LEVEL[f.level]?.[0] ?? label(f.level)}</Badge><span className="text-xs text-muted-foreground">{label(f.category)}</span></div>
                    <p className="mt-1">{f.text}</p>
                    {f.source_url && <a href={f.source_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">{host(f.source_url)}<ExternalLink className="size-3" /></a>}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Activity</CardTitle><CardDescription className="mt-1">Every action and delivery result, also logged on the lead.</CardDescription></CardHeader>
            <CardContent>
              <ol className="grid gap-2 text-sm">
                {seq.events.map((e) => (
                  <li key={e.id} className="grid grid-cols-[1fr_auto] gap-2">
                    <div><span className="font-medium">{label(e.event)}</span>{e.message_id ? <span className="text-muted-foreground"> · email #{e.message_id}</span> : null}
                      {typeof e.data.reason === "string" && <div className="text-xs text-muted-foreground">{e.data.reason}</div>}
                      {typeof e.data.error === "string" && <div className="text-xs text-red-700">{e.data.error}</div>}
                      <div className="text-xs text-muted-foreground">{e.actor?.full_name ?? "System"}</div>
                    </div>
                    <span className="whitespace-nowrap text-xs text-muted-foreground">{relative(e.created_at)}</span>
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
