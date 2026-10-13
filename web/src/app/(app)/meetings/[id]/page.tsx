"use client";
import Link from "next/link";
import { use, useState } from "react";
import { CalendarCheck, Check, Mail, MessageCircle, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { DemoBadge, ErrorState, Field, PageHeader } from "@/components/common";
import { InteractionTimeline } from "@/components/engagement";
import { MeetingStatusBadge } from "@/components/meetings";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, fromLocalInput } from "@/lib/format";
import type { Meeting } from "@/lib/types";
import { cn } from "@/lib/utils";

function fmtSlot(iso: string, tz: string) {
  return new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: tz, timeZoneName: "short" }).format(new Date(iso));
}

export default function MeetingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const toast = useToast();
  const { data: m, error, mutate } = useApi<Meeting>(`/meetings/${id}`);
  const [custom, setCustom] = useState("");
  const [agenda, setAgenda] = useState<string | null>(null);
  const [out, setOut] = useState({ outcome: "positive", notes: "", next_steps: "" });
  const [key, setKey] = useState(0);
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  if (!m) return <div className="grid gap-4"><Skeleton className="h-20" /><Skeleton className="h-96" /></div>;
  const act = async (path: string, init: RequestInit & { json?: unknown }, ok: string) => {
    try { const r = await api<Meeting>(path, init); mutate(r, { revalidate: false }); setKey((k) => k + 1); toast("success", ok); return true; }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); return false; }
  };
  const arranging = m.status === "proposed" || m.status === "awaiting_approval";
  const booked = m.status === "scheduled" || m.status === "confirmed";
  const [publicAgenda, prep] = m.agenda.split("\n\nPrep (internal");
  return (
    <>
      <div className="mb-2 text-sm"><Link href="/meetings" className="text-muted-foreground hover:underline">Meetings</Link></div>
      <PageHeader eyebrow={`Meeting #${m.id}`} title={m.title}
        actions={m.status !== "cancelled" && m.status !== "completed" && m.status !== "no_show" ? (
          <Button variant="outline" onClick={() => { const r = prompt("Why is it cancelled? (recorded on the timeline)"); if (r && r.trim().length >= 3) act(`/meetings/${m.id}/cancel`, { method: "POST", json: { reason: r } }, "Cancelled"); }}><X />Cancel meeting</Button>
        ) : undefined} />
      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <DemoBadge show={m.is_demo} /><MeetingStatusBadge status={m.status} />
        <span>With <span className="font-medium text-foreground">{m.contact_name ?? "—"}</span> · <Link className="text-primary hover:underline" href={`/leads/${m.lead_id}`}>{m.institution_name}</Link></span>
        <span>· organiser {m.organizer?.full_name ?? "—"}</span>
        {m.start_at && <span>· {dateTime(m.start_at)} ({m.duration_minutes} min)</span>}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="grid min-w-0 content-start gap-6 xl:col-span-2">
          {arranging && (
            <Card>
              <CardHeader>
                <CardTitle>1. Send the times</CardTitle>
                <CardDescription className="mt-1">The assistant drafted a reply with these times. Approve and send it, then choose the time they agree to.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-3 text-sm">
                {m.requested_text && <blockquote className="border-l-2 border-pdpurple/40 pl-3 italic text-muted-foreground">“{m.requested_text}”</blockquote>}
                <div className="flex flex-wrap gap-2">
                  {m.sequence_id && <Button size="sm" variant="outline" asChild><Link href={`/outreach/sequences/${m.sequence_id}#m${m.draft_email_id}`}><Mail />Review the email reply</Link></Button>}
                  {m.conversation_id && <Button size="sm" variant="outline" asChild><Link href={`/whatsapp?c=${m.conversation_id}`}><MessageCircle />Review the WhatsApp reply</Link></Button>}
                  <Button size="sm" variant="ghost" onClick={() => act(`/meetings/${m.id}/slots`, { method: "POST", json: {} }, "Times refreshed from the calendar")}><RefreshCw />Refresh times</Button>
                </div>
              </CardContent>
            </Card>
          )}
          {arranging && (
            <Card>
              <CardHeader>
                <CardTitle>2. Choose the agreed time</CardTitle>
                <CardDescription className="mt-1">Times are free in {m.organizer?.full_name ?? "the organiser"}&apos;s calendar, in working hours ({m.timezone}).</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2">
                {m.proposed_slots.length === 0 && <p className="text-sm text-amber-900">No times could be suggested (no approved calendar, or no free time). Enter a time below.</p>}
                {m.proposed_slots.map((s) => {
                  const chosen = m.start_at && new Date(m.start_at).getTime() === new Date(s.start).getTime();
                  return (
                    <button key={s.start} onClick={() => act(`/meetings/${m.id}/choose`, { method: "POST", json: { start: s.start } }, "Time chosen — approve to book it")}
                      className={cn("flex items-center justify-between rounded-lg border p-3 text-left text-sm hover:bg-muted", chosen && "border-pdpurple bg-pdpurple-bg/50")}>
                      <span className="font-medium">{fmtSlot(s.start, m.timezone)}</span>
                      <span className="text-xs text-muted-foreground">{chosen ? "Chosen" : s.reason}</span>
                    </button>
                  );
                })}
                <div className="flex flex-wrap items-end gap-2">
                  <Field label="Another time" htmlFor="mt-c"><Input id="mt-c" type="datetime-local" className="w-56" value={custom} onChange={(e) => setCustom(e.target.value)} /></Field>
                  <Button size="sm" variant="outline" disabled={!custom} onClick={() => act(`/meetings/${m.id}/choose`, { method: "POST", json: { start: fromLocalInput(custom) } }, "Time chosen — approve to book it")}>Use this time</Button>
                </div>
              </CardContent>
            </Card>
          )}
          {m.status === "awaiting_approval" && m.start_at && (
            <Card className="border-pdpurple/40">
              <CardHeader><CardTitle>3. Approve and book</CardTitle><CardDescription className="mt-1">Creates the event in the organiser&apos;s calendar and sends the invitation to the attendees.</CardDescription></CardHeader>
              <CardContent className="flex flex-wrap items-center gap-3">
                <span className="text-sm font-medium">{fmtSlot(m.start_at, m.timezone)}</span>
                <Button onClick={() => act(`/meetings/${m.id}/approve`, { method: "POST" }, "Meeting booked and invitation sent")}><CalendarCheck />Approve and book</Button>
              </CardContent>
            </Card>
          )}
          {booked && (
            <Card>
              <CardHeader><CardTitle>{m.status === "confirmed" ? "Confirmed" : "Booked"}</CardTitle><CardDescription className="mt-1">{m.location ? <>Join: <a className="text-primary hover:underline" href={m.location} target="_blank" rel="noopener noreferrer">{m.location}</a></> : "The calendar adds the meeting link."}</CardDescription></CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {m.status === "scheduled" && <Button variant="outline" onClick={() => act(`/meetings/${m.id}/confirm`, { method: "POST" }, "Marked confirmed")}><Check />They&apos;ve accepted</Button>}
              </CardContent>
            </Card>
          )}
          {(booked || m.status === "completed" || m.status === "no_show") && (
            <Card>
              <CardHeader><CardTitle>Notes, outcome and next steps</CardTitle><CardDescription className="mt-1">Saved to the CRM timeline. The first line of next steps becomes a follow-up task.</CardDescription></CardHeader>
              <CardContent className="grid gap-3">
                {m.outcome && <p className="text-sm">Recorded outcome: <span className="font-medium">{m.outcome}</span>{m.next_steps ? ` · next: ${m.next_steps}` : ""}</p>}
                <Field label="Outcome" htmlFor="mt-o"><Select id="mt-o" value={out.outcome} onChange={(e) => setOut({ ...out, outcome: e.target.value })}><option value="positive">Positive</option><option value="neutral">Neutral</option><option value="negative">Negative</option><option value="no_show">They didn&apos;t attend</option></Select></Field>
                <Field label="Notes" htmlFor="mt-n"><Textarea id="mt-n" rows={4} value={out.notes} onChange={(e) => setOut({ ...out, notes: e.target.value })} /></Field>
                <Field label="Next steps" htmlFor="mt-x" hint="e.g. “Send pilot proposal by Friday”"><Textarea id="mt-x" rows={2} value={out.next_steps} onChange={(e) => setOut({ ...out, next_steps: e.target.value })} /></Field>
                <div><Button onClick={() => act(`/meetings/${m.id}/outcome`, { method: "POST", json: out }, "Outcome saved and follow-up task created")}>Save outcome</Button></div>
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader><CardTitle>Everything with {m.contact_name ?? "this person"}</CardTitle></CardHeader>
            <CardContent>{m.contact_id ? <InteractionTimeline contactId={m.contact_id} refreshKey={key} /> : null}</CardContent>
          </Card>
        </div>

        <div className="grid min-w-0 content-start gap-6">
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0"><CardTitle>Agenda</CardTitle>{arranging && agenda === null && <Button size="sm" variant="ghost" onClick={() => setAgenda(publicAgenda)}>Edit</Button>}</CardHeader>
            <CardContent className="grid gap-3 text-sm">
              {agenda !== null ? (
                <>
                  <Textarea rows={10} value={agenda} onChange={(e) => setAgenda(e.target.value)} />
                  <div className="flex gap-2"><Button size="sm" onClick={async () => { if (await act(`/meetings/${m.id}`, { method: "PATCH", json: { agenda: agenda + (prep ? "\n\nPrep (internal" + prep : "") } }, "Agenda saved")) setAgenda(null); }}>Save</Button><Button size="sm" variant="outline" onClick={() => setAgenda(null)}>Cancel</Button></div>
                </>
              ) : <p className="whitespace-pre-wrap">{publicAgenda}</p>}
              {prep && <div className="rounded-md bg-amber-50 p-2.5 text-xs text-amber-900"><div className="font-semibold">Prep (internal — never sent)</div><p className="mt-1 whitespace-pre-wrap">{prep.replace(/^[^\n]*\n/, "")}</p></div>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Attendees</CardTitle></CardHeader>
            <CardContent><ul className="grid gap-1 text-sm">{m.attendees.map((a, i) => <li key={i}>{a.name} <span className="text-xs text-muted-foreground">{a.email} · {a.kind === "internal" ? "PlugDrop" : "guest"}</span></li>)}</ul></CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
