"use client";
import Link from "next/link";
import { useState } from "react";
import { AlarmClock, ArrowRight, CalendarCheck, PhoneCall, Target, TrendingUp, Building2, Wallet } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DemoBadge, ErrorState, PageHeader, StageBadge } from "@/components/common";
import { useApi } from "@/lib/api";
import { compactMoney, label, money, pct, relative } from "@/lib/format";
import type { Dashboard } from "@/lib/types";
import { cn } from "@/lib/utils";

function Kpi({ label: title, value, sub, icon: Icon, href }: { label: string; value: string; sub?: string; icon: React.ElementType; href?: string }) {
  const body = (
    <Card className="h-full transition-shadow hover:shadow-panel">
      <CardContent className="flex items-start justify-between gap-3 p-5">
        <div className="min-w-0">
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{title}</div>
          <div className="mt-2 font-display text-3xl font-medium tracking-tight text-pdblack tabular">{value}</div>
          {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
        </div>
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-pdpurple-bg"><Icon className="size-4 text-pdpurple-deep" /></div>
      </CardContent>
    </Card>
  );
  return href ? <Link href={href} className="block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-xl">{body}</Link> : body;
}

function Funnel({ data }: { data: Dashboard["funnel"] }) {
  const max = Math.max(1, ...data.map((d) => d.reached));
  return (
    <ol className="grid gap-2">
      {data.map((d) => (
        <li key={d.stage} className="grid grid-cols-[120px_1fr_88px] items-center gap-3 text-sm">
          <span className="truncate text-muted-foreground">{label(d.stage)}</span>
          <div className="h-5 rounded bg-muted" aria-hidden>
            <div className="h-5 rounded bg-pdpurple" style={{ width: `${(d.reached / max) * 100}%`, opacity: 0.35 + 0.65 * (d.reached / max) }} />
          </div>
          <span className="text-right tabular"><span className="font-medium">{d.reached}</span> <span className="text-muted-foreground">· {pct(d.rate)}</span></span>
        </li>
      ))}
    </ol>
  );
}

function WeeklyBars({ data }: { data: Dashboard["weekly_activity"] }) {
  const max = Math.max(1, ...data.map((w) => w.calls + w.emails + w.meetings));
  const series = [
    { key: "emails" as const, label: "Emails", cls: "bg-pdpurple-soft" },
    { key: "calls" as const, label: "Calls", cls: "bg-pdpurple-deep" },
    { key: "meetings" as const, label: "Meetings", cls: "bg-pdblack" },
  ];
  return (
    <div>
      <div className="flex h-40 items-end gap-2" role="img" aria-label="Outreach activity per week for the last 8 weeks">
        {data.map((w) => {
          const total = w.calls + w.emails + w.meetings;
          return (
            <div key={w.week_start} className="flex h-full flex-1 flex-col justify-end gap-px" title={`Week of ${w.week_start}: ${w.calls} calls, ${w.emails} emails, ${w.meetings} meetings`}>
              {series.map((s) => w[s.key] > 0 && (
                <div key={s.key} className={cn("w-full first:rounded-t", s.cls)} style={{ height: `${(w[s.key] / max) * 100}%` }} />
              ))}
              {total === 0 && <div className="h-px w-full bg-border" />}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex gap-2 text-[11px] text-muted-foreground">
        {data.map((w) => <div key={w.week_start} className="flex-1 text-center">{new Date(w.week_start).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</div>)}
      </div>
      <div className="mt-3 flex gap-4 text-xs">
        {series.map((s) => <span key={s.key} className="flex items-center gap-1.5"><span className={cn("size-2.5 rounded-sm", s.cls)} />{s.label}</span>)}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [mine, setMine] = useState(false);
  const [days, setDays] = useState(30);
  const { data, error, mutate, isLoading } = useApi<Dashboard>("/dashboard", { mine, days });

  return (
    <>
      <PageHeader eyebrow="Overview" title="Dashboard" description="Prospects, pipeline and outreach for PlugDrop.AI education sales."
        actions={
          <>
            <div className="inline-flex rounded-lg bg-muted p-1 text-sm" role="group" aria-label="Scope">
              {[{ v: false, t: "Team" }, { v: true, t: "Mine" }].map((o) => (
                <button key={o.t} onClick={() => setMine(o.v)} aria-pressed={mine === o.v}
                  className={cn("rounded-md px-3 py-1 font-medium", mine === o.v ? "bg-background shadow" : "text-muted-foreground")}>{o.t}</button>
              ))}
            </div>
            <select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-9 rounded-md border bg-background px-2.5 text-sm">
              {[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
            </select>
          </>
        } />
      {error && <ErrorState error={error} retry={() => mutate()} />}
      {isLoading && !data && <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div>}
      {data && (
        <div className="grid grid-cols-1 gap-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi label="Total prospects" value={data.total_prospects.toLocaleString()} sub={`${data.total_contacts.toLocaleString()} contacts`} icon={Building2} href="/institutions" />
            <Kpi label="Qualified leads" value={data.qualified_leads.toLocaleString()} sub={`${data.open_leads} open leads`} icon={Target} href="/leads?stage=qualified" />
            <Kpi label="Outreach activity" value={data.outreach_activity.toLocaleString()} sub={`Calls + emails, last ${data.period_days} days`} icon={PhoneCall} />
            <Kpi label="Meetings" value={data.meetings.toLocaleString()} sub={`${data.meetings_booked_leads} leads at Meeting booked`} icon={CalendarCheck} />
            <Kpi label="Open pipeline" value={compactMoney(data.pipeline_value)} sub={`${money(data.won_value)} won`} icon={Wallet} href="/opportunities" />
            <Kpi label="Lead → meeting" value={pct(data.lead_to_meeting_rate)} sub="Share of leads that reached a meeting" icon={TrendingUp} />
            <Kpi label="Win rate" value={pct(data.win_rate)} sub="Won ÷ (won + lost)" icon={TrendingUp} />
            <Kpi label="Tasks due" value={`${data.tasks_due_today}`} sub={data.tasks_overdue ? `${data.tasks_overdue} overdue` : "Nothing overdue"} icon={AlarmClock} href="/tasks?due=overdue" />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-5">
            <Card className="xl:col-span-3">
              <CardHeader><CardTitle>Conversion funnel</CardTitle><CardDescription>Leads that reached at least each stage (including those later lost)</CardDescription></CardHeader>
              <CardContent><Funnel data={data.funnel} /></CardContent>
            </Card>
            <Card className="min-w-0 xl:col-span-2">
              <CardHeader><CardTitle>Outreach per week</CardTitle><CardDescription>Last 8 weeks</CardDescription></CardHeader>
              <CardContent><WeeklyBars data={data.weekly_activity} /></CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
                <div><CardTitle>Top open opportunities</CardTitle></div>
                <Link href="/opportunities" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">All<ArrowRight className="size-3.5" /></Link>
              </CardHeader>
              <CardContent className="grid grid-cols-1 gap-1">
                {data.top_opportunities.length === 0 && <p className="text-sm text-muted-foreground">No open opportunities yet.</p>}
                {data.top_opportunities.map((o) => (
                  <Link key={o.id} href={`/leads/${o.lead_id}`} className="flex items-center justify-between gap-3 rounded-md px-2 py-2 hover:bg-muted">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{o.institution.name} <DemoBadge show={o.is_demo} /></div>
                      <div className="truncate text-xs text-muted-foreground">{o.next_action ?? o.name}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2"><StageBadge stage={o.stage} /><span className="w-20 text-right text-sm font-medium tabular">{money(o.estimated_value, o.currency)}</span></div>
                  </Link>
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Recent activity</CardTitle></CardHeader>
              <CardContent className="grid grid-cols-1 gap-3">
                {data.recent_activity.length === 0 && <p className="text-sm text-muted-foreground">No activity yet.</p>}
                {data.recent_activity.map((a) => (
                  <div key={a.id} className="flex gap-3 text-sm">
                    <span className="mt-1.5 size-2 shrink-0 rounded-full bg-pdpurple" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <div className="truncate">{a.lead_id ? <Link className="hover:underline" href={`/leads/${a.lead_id}`}>{a.subject}</Link> : a.subject}</div>
                      <div className="text-xs text-muted-foreground">{label(a.activity_type)} · {a.user?.full_name ?? "System"} · {relative(a.occurred_at)}</div>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
