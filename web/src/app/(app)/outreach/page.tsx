"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { MailPlus, RefreshCw, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DemoBadge, EmptyState, ErrorState, PageHeader, Pagination, SearchInput } from "@/components/common";
import { MessageStatusBadge, QualityChecks, ReplyLabelBadge, SequenceStatusBadge, StartOutreachDialog } from "@/components/outreach";
import { useCan } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, label, relative } from "@/lib/format";
import type { OutreachOverview, Page, ReplyItem, SuppressedView, WorkItem } from "@/lib/types";

function Stat({ title, value, sub, onClick }: { title: string; value: React.ReactNode; sub?: string; onClick?: () => void }) {
  return (
    <Card className={onClick ? "cursor-pointer transition-colors hover:border-pdpurple/50" : undefined} onClick={onClick}>
      <CardContent className="p-4">
        <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{title}</div>
        <div className="mt-1.5 font-display text-2xl font-medium tabular">{value}</div>
        {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function Who({ w }: { w: WorkItem }) {
  return (
    <>
      <Link href={`/outreach/sequences/${w.sequence.id}`} className="font-medium hover:underline">{w.sequence.institution.name}</Link> <DemoBadge show={w.sequence.is_demo} />
      <div className="text-xs text-muted-foreground">{w.sequence.contact_name}{w.sequence.contact_designation ? ` · ${w.sequence.contact_designation}` : ""}</div>
    </>
  );
}

function stepLabel(w: WorkItem) {
  return w.message.kind === "reply" ? "Reply" : w.message.step === 1 ? "First email" : `Follow-up ${w.message.step - 1}`;
}

function MessageList({ view, refresh }: { view: "drafts" | "scheduled" | "sent"; refresh: number }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const { data, error, mutate, isLoading } = useApi<Page<WorkItem>>("/outreach/messages", { view, page, page_size: 25, q, r: refresh });
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  const empty = {
    drafts: ["No drafts waiting", "Start outreach for a researched prospect to draft emails."],
    scheduled: ["Nothing approved or scheduled", "Approved emails wait here for the sending window."],
    sent: ["Nothing sent yet", "Sent emails, delivery results and bounces appear here."],
  }[view];
  return (
    <>
      <div className="mb-3"><SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="Search institution or email…" /></div>
      <Card>
        <Table>
          <THead><TR className="hover:bg-transparent">
            <TH>Prospect</TH><TH>Email</TH><TH>Status</TH>
            <TH>{view === "sent" ? "Sent" : view === "scheduled" ? "Due" : "Checks"}</TH><TH />
          </TR></THead>
          <TBody>
            {isLoading && !data && <TR><TD colSpan={5}><Skeleton className="h-5" /></TD></TR>}
            {data?.items.map((w) => (
              <TR key={w.message.id}>
                <TD><Who w={w} /></TD>
                <TD className="max-w-md"><div className="text-xs text-muted-foreground">{stepLabel(w)}</div><div className="truncate">{w.message.subject}</div></TD>
                <TD><MessageStatusBadge status={w.message.status} />{w.message.last_error && <div className="mt-1 max-w-64 text-xs text-amber-800">{w.message.last_error}</div>}</TD>
                <TD className="whitespace-nowrap text-sm text-muted-foreground">
                  {view === "sent" ? (w.message.sent_at ? dateTime(w.message.sent_at) : "—")
                    : view === "scheduled" ? (w.message.scheduled_for ? relative(w.message.scheduled_for) : "After previous email")
                    : <QualityChecks quality={w.message.quality} compact />}
                </TD>
                <TD className="text-right"><Button size="sm" variant="outline" asChild><Link href={`/outreach/sequences/${w.sequence.id}#m${w.message.id}`}>{view === "drafts" ? "Review" : "Open"}</Link></Button></TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {data && data.total === 0 && <EmptyState title={empty[0]} description={empty[1]} />}
        {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Card>
    </>
  );
}

function Replies({ refresh }: { refresh: number }) {
  const [page, setPage] = useState(1);
  const { data, error, mutate } = useApi<Page<ReplyItem>>("/outreach/replies", { attention: true, page, page_size: 25, r: refresh });
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  return (
    <Card>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>From</TH><TH>Reply</TH><TH>Classified as</TH><TH>Received</TH><TH /></TR></THead>
        <TBody>
          {data?.items.map(({ reply: r, sequence: s }) => (
            <TR key={r.id}>
              <TD>{s ? <Link href={`/outreach/sequences/${s.id}`} className="font-medium hover:underline">{s.institution.name}</Link> : "Unmatched"}<div className="text-xs text-muted-foreground">{r.from_email}</div></TD>
              <TD className="max-w-md"><div className="truncate font-medium">{r.subject}</div><div className="line-clamp-2 text-xs text-muted-foreground">{r.summary ?? r.body}</div></TD>
              <TD><ReplyLabelBadge value={r.label} optOut={r.opt_out} /><div className="mt-0.5 text-[11px] text-muted-foreground">{r.label_source === "claude" ? "AI" : r.label_source === "human" ? "Set by a person" : "Rules"} · {r.label_confidence}%</div></TD>
              <TD className="whitespace-nowrap text-sm text-muted-foreground">{relative(r.received_at)}</TD>
              <TD className="text-right">{s && <Button size="sm" variant="outline" asChild><Link href={`/outreach/sequences/${s.id}#r${r.id}`}>Handle</Link></Button>}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.total === 0 && <EmptyState title="No replies need attention" description="Replies are matched to their sequence, classified, and every follow-up stops automatically." />}
      {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
    </Card>
  );
}

function FollowUps({ refresh }: { refresh: number }) {
  const { data, error, mutate } = useApi<WorkItem[]>("/outreach/followups-due", { r: refresh });
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  return (
    <Card>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Prospect</TH><TH>Follow-up</TH><TH>Status</TH><TH>Due</TH><TH /></TR></THead>
        <TBody>
          {data?.map((w) => (
            <TR key={w.message.id}>
              <TD><Who w={w} /></TD>
              <TD className="max-w-md"><div className="text-xs text-muted-foreground">{stepLabel(w)}</div><div className="truncate">{w.message.subject}</div></TD>
              <TD><MessageStatusBadge status={w.message.status} /></TD>
              <TD className="whitespace-nowrap text-sm">{w.due_at ? relative(w.due_at) : "—"}</TD>
              <TD className="text-right"><Button size="sm" variant="outline" asChild><Link href={`/outreach/sequences/${w.sequence.id}#m${w.message.id}`}>{w.message.status === "draft" ? "Review" : "Open"}</Link></Button></TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.length === 0 && <EmptyState title="No follow-ups due" description="Follow-ups appear here when the previous email has gone out and the wait time is nearly over." />}
    </Card>
  );
}

function Suppressed({ refresh }: { refresh: number }) {
  const { data, error, mutate } = useApi<SuppressedView>("/outreach/suppressed", { r: refresh });
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  if (!data) return <Skeleton className="h-40" />;
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>Opt-outs and suppression</CardTitle></CardHeader>
        <Table>
          <THead><TR className="hover:bg-transparent"><TH>Who</TH><TH>Status</TH><TH>Reason</TH><TH>When</TH></TR></THead>
          <TBody>
            {data.consent_records.map((c) => (
              <TR key={c.id}>
                <TD className="font-mono text-xs">{c.email ?? (c.domain ? `@${c.domain}` : `contact #${c.contact_id}`)}</TD>
                <TD><Badge variant={c.status === "opted_out" ? "danger" : "warning"}>{label(c.status)}</Badge></TD>
                <TD className="max-w-56 truncate text-xs text-muted-foreground" title={c.reason ?? ""}>{c.reason ?? c.source ?? "—"}</TD>
                <TD className="whitespace-nowrap text-xs text-muted-foreground">{relative(c.recorded_at)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {data.consent_records.length === 0 && <EmptyState title="No opt-outs" />}
        <CardContent className="pt-3 text-xs text-muted-foreground">Manage opt-outs in <Link href="/settings" className="text-primary hover:underline">Settings → Opt-outs</Link>.</CardContent>
      </Card>
      <div className="grid content-start gap-6">
        <Card>
          <CardHeader><CardTitle>Stopped sequences</CardTitle></CardHeader>
          <Table>
            <TBody>
              {data.stopped_sequences.map((s) => (
                <TR key={s.id}>
                  <TD><Link href={`/outreach/sequences/${s.id}`} className="font-medium hover:underline">{s.institution.name}</Link><div className="text-xs text-muted-foreground">{s.contact_name}</div></TD>
                  <TD><SequenceStatusBadge status={s.status} reason={s.stop_reason} /></TD>
                  <TD className="whitespace-nowrap text-xs text-muted-foreground">{s.stopped_at ? relative(s.stopped_at) : ""}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
          {data.stopped_sequences.length === 0 && <EmptyState title="None stopped for opt-out, bounce or fit" />}
        </Card>
        <Card>
          <CardHeader><CardTitle>Bounced or invalid emails</CardTitle></CardHeader>
          <Table>
            <TBody>
              {data.bounced_contacts.map((c) => (
                <TR key={c.id}><TD>{c.name}<div className="text-xs text-muted-foreground">{c.institution}</div></TD><TD className="font-mono text-xs">{c.email}</TD><TD><Badge variant="danger">{label(c.status)}</Badge></TD></TR>
              ))}
            </TBody>
          </Table>
          {data.bounced_contacts.length === 0 && <EmptyState title="No bounces" />}
        </Card>
      </div>
    </div>
  );
}

const TABS = ["drafts", "scheduled", "sent", "replies", "followups", "suppressed"] as const;

export default function OutreachPage() {
  const toast = useToast();
  const isManager = useCan("manager");
  const [tab, setTab] = useState<string>("drafts");
  const [refresh, setRefresh] = useState(0);
  const [start, setStart] = useState(false);
  const { data: ov, mutate } = useApi<OutreachOverview>("/outreach/overview", { r: refresh }, { refreshInterval: 30000 });
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t && (TABS as readonly string[]).includes(t)) setTab(t);
  }, []);
  const go = (t: string) => { setTab(t); window.history.replaceState(null, "", `?tab=${t}`); };
  const sync = async () => {
    try {
      const r = await api<{ dispatched: number; inbound?: number; replies?: number }>("/outreach/sync", { method: "POST" });
      toast("success", `Checked mailboxes. ${r.dispatched} email(s) processed${r.replies ? `, ${r.replies} new repl${r.replies === 1 ? "y" : "ies"}` : ""}.`);
      setRefresh((k) => k + 1); mutate();
    } catch (e) { toast("error", e instanceof Error ? e.message : "Sync failed"); }
  };
  return (
    <>
      <PageHeader eyebrow="AI outreach agent" title="Outreach"
        description="Personalised emails drafted from approved research. A person approves every email before it goes out."
        actions={<>
          {isManager && <Button variant="outline" onClick={sync}><RefreshCw />Check mailboxes</Button>}
          <Button onClick={() => setStart(true)}><MailPlus />Start outreach</Button>
        </>} />
      {ov && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
          <ShieldCheck className="size-4 text-pdpurple-deep" />
          {ov.sending_mode === "draft_only"
            ? <span><span className="font-medium">Draft-only mode.</span> Approved emails are placed in the mailbox as drafts for a person to send.</span>
            : <span><span className="font-medium">Sending mode.</span> Approved emails send automatically in the sending window from mailboxes an admin has enabled.</span>}
          {!ov.external_sending_allowed && <Badge variant="warning">Real mailboxes can&apos;t send yet — switched off on the server</Badge>}
          <Badge variant="muted">Writer: {ov.writer === "claude" ? "Claude" : "rule-based templates"}</Badge>
          {ov.mailboxes_active === 0 && <Badge variant="danger">No approved mailbox</Badge>}
        </div>
      )}
      {ov && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat title="Drafts to approve" value={ov.drafts} onClick={() => go("drafts")} />
          <Stat title="Approved / scheduled" value={ov.scheduled} sub={ov.blocked ? `${ov.blocked} held back` : undefined} onClick={() => go("scheduled")} />
          <Stat title="Sent (7 days)" value={ov.sent_7d} onClick={() => go("sent")} />
          <Stat title="Replies to handle" value={ov.replies_attention} onClick={() => go("replies")} />
          <Stat title="Follow-ups due" value={ov.followups_due} onClick={() => go("followups")} />
          <Stat title="Live sequences" value={ov.live_sequences} />
        </div>
      )}
      <Tabs value={tab} onValueChange={go}>
        <TabsList className="mb-4 h-auto flex-wrap">
          <TabsTrigger value="drafts">Drafts awaiting approval</TabsTrigger>
          <TabsTrigger value="scheduled">Approved & scheduled</TabsTrigger>
          <TabsTrigger value="sent">Sent & delivery</TabsTrigger>
          <TabsTrigger value="replies">Replies{ov?.replies_attention ? ` (${ov.replies_attention})` : ""}</TabsTrigger>
          <TabsTrigger value="followups">Follow-ups due</TabsTrigger>
          <TabsTrigger value="suppressed">Opt-outs & suppressed</TabsTrigger>
        </TabsList>
        <TabsContent value="drafts" className="mt-0"><MessageList view="drafts" refresh={refresh} /></TabsContent>
        <TabsContent value="scheduled" className="mt-0"><MessageList view="scheduled" refresh={refresh} /></TabsContent>
        <TabsContent value="sent" className="mt-0"><MessageList view="sent" refresh={refresh} /></TabsContent>
        <TabsContent value="replies" className="mt-0"><Replies refresh={refresh} /></TabsContent>
        <TabsContent value="followups" className="mt-0"><FollowUps refresh={refresh} /></TabsContent>
        <TabsContent value="suppressed" className="mt-0"><Suppressed refresh={refresh} /></TabsContent>
      </Tabs>
      <StartOutreachDialog open={start} onOpenChange={setStart} />
    </>
  );
}
