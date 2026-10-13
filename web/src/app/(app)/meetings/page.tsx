"use client";
import Link from "next/link";
import { CalendarCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DemoBadge, EmptyState, ErrorState, PageHeader } from "@/components/common";
import { useApi } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import type { Meeting } from "@/lib/types";
import { MeetingStatusBadge } from "@/components/meetings";

function Section({ title, hint, items }: { title: string; hint: string; items: Meeting[] }) {
  return (
    <Card>
      <CardHeader><CardTitle>{title} ({items.length})</CardTitle><CardDescription className="mt-1">{hint}</CardDescription></CardHeader>
      <CardContent className="grid gap-2">
        {items.length === 0 && <p className="text-sm text-muted-foreground">Nothing here.</p>}
        {items.map((m) => (
          <Link key={m.id} href={`/meetings/${m.id}`} className="rounded-lg border p-3 text-sm hover:bg-muted">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">{m.institution_name} <DemoBadge show={m.is_demo} /></span>
              <MeetingStatusBadge status={m.status} />
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {m.contact_name ?? "—"} · {m.start_at ? `${dateTime(m.start_at)} (${relative(m.start_at)})` : `${m.proposed_slots.length} time(s) suggested`} · from {m.source === "email_reply" ? "an email reply" : m.source === "whatsapp" ? "WhatsApp" : "a note"}
            </div>
          </Link>
        ))}
      </CardContent>
    </Card>
  );
}

export default function MeetingsPage() {
  const { data, error, mutate } = useApi<Meeting[]>("/meetings", undefined, { refreshInterval: 30000 });
  const now = Date.now();
  const by = (f: (m: Meeting) => boolean) => (data ?? []).filter(f);
  return (
    <>
      <PageHeader eyebrow="Meeting assistant" title="Meetings"
        description="Suggested agendas and times when a prospect is interested. A calendar event is created only after someone approves it." />
      {error && <ErrorState error={error} retry={() => mutate()} />}
      {!data ? <Skeleton className="h-64" /> : data.length === 0 ? (
        <Card><EmptyState title="No meetings yet" description="When someone replies with interest, the assistant suggests times here. You can also propose one from a lead." action={<Button asChild variant="outline"><Link href="/leads"><CalendarCheck />Go to leads</Link></Button>} /></Card>
      ) : (
        <div className="grid gap-6 xl:grid-cols-2">
          <Section title="To arrange" hint="Send the suggested times, then choose the agreed time." items={by((m) => m.status === "proposed")} />
          <Section title="Needs approval" hint="A time is chosen; approving creates the calendar invitation." items={by((m) => m.status === "awaiting_approval")} />
          <Section title="Upcoming" hint="Booked meetings. Confirm once they accept the invitation." items={by((m) => ["scheduled", "confirmed"].includes(m.status) && !!m.end_at && new Date(m.end_at).getTime() > now)} />
          <Section title="Record the outcome" hint="Meetings that have ended without notes, outcome and next steps." items={by((m) => ["scheduled", "confirmed"].includes(m.status) && !!m.end_at && new Date(m.end_at).getTime() <= now)} />
          <Section title="Done" hint="Held, cancelled or missed." items={by((m) => ["completed", "cancelled", "no_show"].includes(m.status)).slice(0, 20)} />
        </div>
      )}
    </>
  );
}
