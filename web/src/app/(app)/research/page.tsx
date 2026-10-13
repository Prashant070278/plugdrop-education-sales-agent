"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw, RotateCcw, Save, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DemoBadge, EmptyState, ErrorState, Field, FilterSelect, PageHeader, Pagination, SearchInput, useListState } from "@/components/common";
import { RunStatusBadge, ScorePill } from "@/components/research";
import { useCan } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, label, relative } from "@/lib/format";
import type { Page, ResearchConfig, ResearchJob, ResearchJobDetail, ResearchOverview, ResearchRunSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

function Stat({ title, value, sub }: { title: string; value: React.ReactNode; sub?: string }) {
  return (
    <Card><CardContent className="p-4">
      <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{title}</div>
      <div className="mt-1.5 font-display text-2xl font-medium tabular">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </CardContent></Card>
  );
}

function Results({ refreshKey }: { refreshKey: number }) {
  const [s, set] = useListState({ page: "1" });
  const { data, error, mutate, isLoading } = useApi<Page<ResearchRunSummary>>("/research/runs", { q: s.q, tier: s.tier, page: s.page, page_size: 25, r: refreshKey });
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-2">
        <SearchInput value={s.q ?? ""} onChange={(q) => set({ q })} placeholder="Search institution…" />
        <FilterSelect ariaLabel="Tier" allLabel="All tiers" value={s.tier ?? ""} onChange={(tier) => set({ tier })} options={["A", "B", "C", "D"].map((t) => ({ value: t, label: `Tier ${t}` }))} />
      </div>
      {error && <ErrorState error={error} retry={() => mutate()} />}
      <Card>
        <Table>
          <THead><TR className="hover:bg-transparent"><TH>Institution</TH><TH>Score</TH><TH>Confidence</TH><TH>Status</TH><TH>Researched</TH><TH>CRM</TH><TH /></TR></THead>
          <TBody>
            {isLoading && !data && <TR><TD colSpan={7}><Skeleton className="h-5" /></TD></TR>}
            {data?.items.map((r) => (
              <TR key={r.id}>
                <TD><Link href={`/institutions/${r.institution.id}`} className="font-medium hover:underline">{r.institution.name}</Link> <DemoBadge show={r.institution.is_demo} /></TD>
                <TD><ScorePill score={r.score} tier={r.tier} /></TD>
                <TD>{r.confidence ? label(r.confidence) : "—"}</TD>
                <TD><RunStatusBadge status={r.status} />{r.last_error && <div className="mt-1 max-w-56 truncate text-xs text-red-700" title={r.last_error}>{r.last_error}</div>}</TD>
                <TD className="text-muted-foreground">{r.finished_at ? relative(r.finished_at) : "—"}</TD>
                <TD>{r.pushed_at ? <Badge variant="success">Pushed</Badge> : r.status === "succeeded" ? <Badge variant="warning">To review</Badge> : "—"}</TD>
                <TD className="text-right">{r.status === "succeeded" && <Button size="sm" variant="outline" asChild><Link href={`/research/runs/${r.id}`}>Review</Link></Button>}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {data && data.total === 0 && <EmptyState title="No research yet" description="Select institutions on the Institutions page and choose Research selected." action={<Button asChild variant="outline"><Link href="/institutions">Go to Institutions</Link></Button>} />}
        {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={(p) => set({ page: String(p) })} />}
      </Card>
    </>
  );
}

function JobRow({ job, onChange }: { job: ResearchJob; onChange: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const active = job.status === "queued" || job.status === "running";
  const { data, mutate } = useApi<ResearchJobDetail>(open ? `/research/jobs/${job.id}` : null, undefined, { refreshInterval: active && open ? 2000 : 0 });
  const done = (job.counts.succeeded ?? 0) + (job.counts.failed ?? 0) + (job.counts.cancelled ?? 0);
  const act = async (path: string, msg: string) => {
    try { await api(path, { method: "POST" }); toast("success", msg); mutate(); onChange(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <>
      <TR className="cursor-pointer" onClick={() => setOpen(!open)}>
        <TD className="w-8">{open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</TD>
        <TD><div className="font-medium">{job.label}</div><div className="text-xs text-muted-foreground">#{job.id} · {job.requested_by?.full_name ?? "—"} · {dateTime(job.created_at)}</div></TD>
        <TD><RunStatusBadge status={job.status} /></TD>
        <TD>
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted"><div className="h-full bg-pdpurple" style={{ width: `${(done / Math.max(1, job.total_items)) * 100}%` }} /></div>
            <span className="text-xs tabular text-muted-foreground">{done}/{job.total_items}{job.counts.failed ? ` · ${job.counts.failed} failed` : ""}</span>
          </div>
        </TD>
        <TD className="text-muted-foreground">{job.provider} + {job.reasoner}</TD>
        <TD className="text-right tabular">${Number(job.cost_usd).toFixed(4)}</TD>
        <TD className="text-right" onClick={(e) => e.stopPropagation()}>
          {active && <Button size="sm" variant="ghost" onClick={() => act(`/research/jobs/${job.id}/cancel`, "Job cancelled")}><XCircle />Cancel</Button>}
        </TD>
      </TR>
      {open && (
        <TR className="hover:bg-transparent">
          <TD colSpan={7} className="bg-muted/40 p-0">
            {!data ? <Skeleton className="m-3 h-10" /> : (
              <table className="w-full text-sm">
                <tbody>
                  {data.runs.map((r) => (
                    <tr key={r.id} className="border-t">
                      <td className="px-10 py-2">{r.institution.name}</td>
                      <td className="px-3 py-2"><RunStatusBadge status={r.status} /></td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">Attempt {r.attempts}/{r.max_attempts}{r.status === "queued" && r.attempts > 0 ? ` · retry ${relative(r.next_attempt_at)}` : ""}</td>
                      <td className="max-w-xs truncate px-3 py-2 text-xs text-red-700" title={r.last_error ?? ""}>{r.last_error}</td>
                      <td className="px-3 py-2"><ScorePill score={r.score} tier={r.tier} /></td>
                      <td className="px-3 py-2 text-right text-xs tabular text-muted-foreground">${Number(r.cost_usd).toFixed(4)} · {r.input_tokens + r.output_tokens} tokens</td>
                      <td className="px-3 py-2 text-right">
                        {r.status === "succeeded" && <Button size="sm" variant="outline" asChild><Link href={`/research/runs/${r.id}`}>Review</Link></Button>}
                        {(r.status === "failed" || r.status === "cancelled") && <Button size="sm" variant="outline" onClick={() => act(`/research/runs/${r.id}/retry`, "Retry queued")}><RotateCcw />Retry</Button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </TD>
        </TR>
      )}
    </>
  );
}

function Jobs({ refreshKey }: { refreshKey: number }) {
  const [page, setPage] = useState(1);
  const { data, error, mutate } = useApi<Page<ResearchJob>>("/research/jobs", { page, page_size: 20, r: refreshKey }, {
    refreshInterval: (d) => (d?.items.some((j) => j.status === "queued" || j.status === "running") ? 2000 : 0),
  });
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  return (
    <Card>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH /><TH>Job</TH><TH>Status</TH><TH>Progress</TH><TH>Source + reasoning</TH><TH className="text-right">Cost</TH><TH /></TR></THead>
        <TBody>{data?.items.map((j) => <JobRow key={j.id} job={j} onChange={() => mutate()} />)}</TBody>
      </Table>
      {data && data.total === 0 && <EmptyState title="No research jobs yet" />}
      {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
    </Card>
  );
}

function Scoring() {
  const toast = useToast();
  const canEdit = useCan("manager");
  const { data, mutate } = useApi<ResearchConfig>("/research/config");
  const [f, setF] = useState<ResearchConfig | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setF(data); }, [data]);
  if (!f) return <Skeleton className="h-64" />;
  const total = Object.values(f.weights).reduce((a, b) => a + Number(b || 0), 0);
  const W = [
    ["fit", "Institution fit", "Type, size, campuses, confirmed profile"],
    ["problem", "Evidence of a relevant business problem", "Enquiry volumes, slow responses, multi-campus admissions"],
    ["intent", "Buying intent or timing", "Intakes, campaigns, launches, hiring, CRM/telephony projects"],
    ["access", "Access to a relevant decision-maker", "Admissions, recruitment, CIO/CTO, CRM, contact centre, COO"],
    ["quality", "Evidence quality and freshness", "Share of verified facts, recency, number of sources"],
  ] as const;
  const save = async () => {
    setBusy(true);
    try {
      const r = await api<{ rescored: number }>("/research/config", { method: "PUT", json: f });
      toast("success", `Saved. ${r.rescored} research results re-scored.`);
      mutate();
    } catch (e) { toast("error", e instanceof Error ? e.message : "Save failed"); } finally { setBusy(false); }
  };
  const num = (v: string) => (v === "" ? 0 : Number(v));
  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle>Score weights</CardTitle>
          <CardDescription>Weights are scaled to total 100. Missing information always scores zero, hypotheses never count, and older or weakly supported evidence is discounted.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {W.map(([k, title, hint]) => (
            <div key={k} className="grid grid-cols-[1fr_90px_60px] items-center gap-3">
              <div><div className="text-sm font-medium">{title}</div><div className="text-xs text-muted-foreground">{hint}</div></div>
              <Input aria-label={`${title} weight`} type="number" min={0} max={100} disabled={!canEdit} value={f.weights[k]}
                onChange={(e) => setF({ ...f, weights: { ...f.weights, [k]: num(e.target.value) } })} />
              <span className="text-right text-sm tabular text-muted-foreground">{total ? Math.round((f.weights[k] / total) * 100) : 0} pts</span>
            </div>
          ))}
          <div className={cn("text-sm", total === 100 ? "text-muted-foreground" : "text-amber-800")}>Total entered: {total}{total !== 100 && " — will be scaled to 100"}</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Tiers and freshness</CardTitle></CardHeader>
        <CardContent className="grid gap-3">
          <div className="grid grid-cols-3 gap-2">
            {(["A", "B", "C"] as const).map((t) => (
              <Field key={t} label={`Tier ${t} from`} htmlFor={`tier-${t}`}><Input id={`tier-${t}`} type="number" min={1} max={100} disabled={!canEdit} value={f.tiers[t]} onChange={(e) => setF({ ...f, tiers: { ...f.tiers, [t]: num(e.target.value) } })} /></Field>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">Below Tier C is Tier D.</p>
          <Field label="Evidence counts in full up to (days)" htmlFor="fresh"><Input id="fresh" type="number" disabled={!canEdit} value={f.evidence_fresh_days} onChange={(e) => setF({ ...f, evidence_fresh_days: num(e.target.value) })} /></Field>
          <Field label="Evidence is stale after (days)" htmlFor="stale"><Input id="stale" type="number" disabled={!canEdit} value={f.evidence_stale_days} onChange={(e) => setF({ ...f, evidence_stale_days: num(e.target.value) })} /></Field>
          <Field label="Re-run research after (days)" htmlFor="rerun"><Input id="rerun" type="number" disabled={!canEdit} value={f.rerun_after_days} onChange={(e) => setF({ ...f, rerun_after_days: num(e.target.value) })} /></Field>
          {canEdit ? <Button onClick={save} disabled={busy}><Save />Save and re-score</Button> : <p className="text-xs text-muted-foreground">Managers can change scoring.</p>}
        </CardContent>
      </Card>
    </div>
  );
}

export default function ResearchPage() {
  const toast = useToast();
  const [key, setKey] = useState(0);
  const [tab, setTab] = useState("results");
  const { data: ov, mutate } = useApi<ResearchOverview>("/research/overview", { r: key }, { refreshInterval: (d) => (d && (d.queued || d.running) ? 2000 : 0) });
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t) setTab(t);
  }, []);
  const rerunStale = async () => {
    try {
      const j = await api<ResearchJob>("/research/jobs/stale", { method: "POST", json: { limit: 25 } });
      toast("success", `Re-running research for ${j.total_items} institutions`);
      setKey((k) => k + 1); setTab("jobs"); mutate();
    } catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <>
      <PageHeader eyebrow="AI prospect research" title="Research"
        description="Finds and scores education prospects from cited sources. Nothing reaches the CRM until a person approves it."
        actions={<Button variant="outline" onClick={rerunStale} disabled={!ov?.stale}><RefreshCw />Re-run stale{ov?.stale ? ` (${Math.min(ov.stale, 25)})` : ""}</Button>} />
      {ov && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat title="Researched" value={ov.researched} sub={`A ${ov.tiers.A} · B ${ov.tiers.B} · C ${ov.tiers.C} · D ${ov.tiers.D}`} />
          <Stat title="In queue" value={ov.queued + ov.running} sub={ov.running ? `${ov.running} running` : "Idle"} />
          <Stat title="To review" value={ov.pending_review} sub="Sourced findings awaiting approval" />
          <Stat title="Stale or never run" value={ov.stale} sub={`Older than ${ov.rerun_after_days} days`} />
          <Stat title="Spent today" value={`$${ov.spent_today_usd.toFixed(2)}`} sub={`Daily budget $${ov.daily_budget_usd.toFixed(2)}`} />
          <Stat title="Sources" value={<span className="text-base">{label(ov.provider)}</span>} sub={ov.reasoner === "claude" ? `Claude (${ov.model})` : "Rule-based reasoning (no API key)"} />
        </div>
      )}
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4"><TabsTrigger value="results">Results</TabsTrigger><TabsTrigger value="jobs">Jobs & queue</TabsTrigger><TabsTrigger value="scoring">Scoring</TabsTrigger></TabsList>
        <TabsContent value="results" className="mt-0"><Results refreshKey={key} /></TabsContent>
        <TabsContent value="jobs" className="mt-0"><Jobs refreshKey={key} /></TabsContent>
        <TabsContent value="scoring" className="mt-0"><Scoring /></TabsContent>
      </Tabs>
    </>
  );
}
