"use client";
import Link from "next/link";
import { use, useMemo, useState } from "react";
import { AlertTriangle, Check, CheckCheck, ExternalLink, Lightbulb, Send, Target, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DemoBadge, ErrorState, PageHeader } from "@/components/common";
import { EvidenceLevelBadge, ResearchButton, RunStatusBadge, TierBadge } from "@/components/research";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { date, dateTime, label, relative } from "@/lib/format";
import type { ResearchCandidate, ResearchEvidence, ResearchRunDetail, ReviewStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

function host(url: string | null) {
  if (!url) return "";
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

function ReviewButtons({ status, locked, disabled, onSet, name }: { status: ReviewStatus; locked: boolean; disabled?: boolean; onSet: (s: ReviewStatus) => void; name: string }) {
  if (locked) return <Badge variant="success"><Check className="size-3" />In CRM</Badge>;
  return (
    <div className="flex shrink-0 gap-1" role="group" aria-label={`Review ${name}`}>
      <Button size="sm" variant={status === "approved" ? "default" : "outline"} disabled={disabled} aria-pressed={status === "approved"}
        onClick={() => onSet(status === "approved" ? "pending" : "approved")} title={disabled ? "Hypotheses can't be approved — verify them first" : "Approve"}>
        <Check />{status === "approved" ? "Approved" : "Approve"}
      </Button>
      <Button size="sm" variant={status === "rejected" ? "destructive" : "ghost"} aria-pressed={status === "rejected"}
        onClick={() => onSet(status === "rejected" ? "pending" : "rejected")} aria-label="Reject" title="Reject"><X /></Button>
    </div>
  );
}

function EvidenceItem({ e, onSet }: { e: ResearchEvidence; onSet: (s: ReviewStatus) => void }) {
  return (
    <li className={cn("rounded-lg border p-3.5", e.review_status === "rejected" && "opacity-60", e.review_status === "approved" && "border-pdpurple/40 bg-pdpurple-bg/40")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <Badge variant="outline">{e.category_label}</Badge>
            <EvidenceLevelBadge level={e.evidence_level} />
            <span className="text-xs text-muted-foreground">confidence {e.confidence}%</span>
          </div>
          <p className="text-sm font-medium">{e.claim}</p>
          {e.excerpt && <blockquote className="mt-1.5 border-l-2 border-pdpurple/40 pl-3 text-sm italic text-muted-foreground">“{e.excerpt}”</blockquote>}
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {e.source_url
              ? <a href={e.source_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">{e.source_title ?? host(e.source_url)} · {host(e.source_url)}<ExternalLink className="size-3" /></a>
              : <span className="text-amber-800">No source — needs verification</span>}
            {e.source_url && <span>Published {e.published_at ? date(e.published_at) : "date unknown"}</span>}
            {e.retrieved_at && <span>Retrieved {date(e.retrieved_at)}</span>}
          </div>
        </div>
        <ReviewButtons status={e.review_status} locked={!!e.pushed_at} disabled={e.evidence_level === "hypothesis"} onSet={onSet} name={e.claim} />
      </div>
    </li>
  );
}

const EMAIL_LABEL = {
  provider_verified: { t: "Verified by provider", v: "success" as const },
  published: { t: "Published on source", v: "secondary" as const },
  none: { t: "No email", v: "muted" as const },
};

function CandidateRow({ c, onSet }: { c: ResearchCandidate; onSet: (s: ReviewStatus) => void }) {
  return (
    <li className={cn("rounded-lg border p-3.5", c.review_status === "rejected" && "opacity-60", c.review_status === "approved" && "border-pdpurple/40 bg-pdpurple-bg/40")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{c.full_name}</span>
            {c.buying_role_label && <Badge variant="secondary">{c.buying_role_label}</Badge>}
            <Badge variant={c.verification_status === "verified" ? "success" : "muted"}>{label(c.verification_status)}</Badge>
          </div>
          <div className="text-sm text-muted-foreground">{c.designation}{c.department ? ` · ${c.department}` : ""}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
            {c.email ? <span className="font-mono text-xs">{c.email}</span> : null}
            <Badge variant={EMAIL_LABEL[c.email_status].v}>{EMAIL_LABEL[c.email_status].t}</Badge>
          </div>
          {c.excerpt && <blockquote className="mt-1.5 border-l-2 border-pdpurple/40 pl-3 text-xs italic text-muted-foreground">“{c.excerpt}”</blockquote>}
          <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            <a href={c.source_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">{host(c.source_url)}<ExternalLink className="size-3" /></a>
            {c.retrieved_at && <span>Retrieved {date(c.retrieved_at)}</span>}
          </div>
          {c.existing_contact_id && <div className="mt-1 text-xs text-amber-900">Already in the CRM — approving links to the existing contact.</div>}
          {c.notes && <div className="mt-1 text-xs text-amber-900">{c.notes}</div>}
        </div>
        <ReviewButtons status={c.review_status} locked={!!c.pushed_at} onSet={onSet} name={c.full_name} />
      </div>
    </li>
  );
}

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const toast = useToast();
  const { data: run, error, mutate } = useApi<ResearchRunDetail>(`/research/runs/${id}`);
  const [busy, setBusy] = useState(false);
  const [showLog, setShowLog] = useState(false);

  const groups = useMemo(() => {
    const ev = run?.evidence ?? [];
    return {
      signal: ev.filter((e) => e.kind === "signal" && e.evidence_level !== "hypothesis"),
      use_case: ev.filter((e) => e.kind === "use_case" && e.evidence_level !== "hypothesis"),
      profile: ev.filter((e) => e.kind === "profile"),
      hypothesis: ev.filter((e) => e.evidence_level === "hypothesis"),
    };
  }, [run]);

  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  if (!run) return <div className="grid gap-4"><Skeleton className="h-20" /><Skeleton className="h-96" /></div>;

  const review = async (body: { evidence?: Record<number, ReviewStatus>; candidates?: Record<number, ReviewStatus> }) => {
    try {
      const updated = await api<ResearchRunDetail>(`/research/runs/${run.id}/review`, { method: "POST", json: body });
      mutate(updated, { revalidate: false });
    } catch (e) { toast("error", e instanceof Error ? e.message : "Review failed"); }
  };
  const approveVerified = () => {
    const ids = run.evidence.filter((e) => e.evidence_level === "verified_fact" && e.review_status === "pending" && !e.pushed_at);
    if (!ids.length) { toast("error", "No pending verified facts"); return; }
    review({ evidence: Object.fromEntries(ids.map((e) => [e.id, "approved"])) });
  };
  const toPushF = run.evidence.filter((e) => e.review_status === "approved" && !e.pushed_at).length;
  const toPushC = run.candidates.filter((c) => c.review_status === "approved" && !c.pushed_at).length;
  const push = async () => {
    setBusy(true);
    try {
      const r = await api<{ findings: number; contacts_created: number; contacts_linked: number; skipped: string[] }>(`/research/runs/${run.id}/push`, { method: "POST" });
      toast("success", `Added ${r.findings} findings and ${r.contacts_created} new contacts${r.contacts_linked ? ` (${r.contacts_linked} linked to existing)` : ""} to the CRM.`);
      if (r.skipped.length) toast("error", `Skipped: ${r.skipped.join("; ")}`);
      mutate();
    } catch (e) { toast("error", e instanceof Error ? e.message : "Push failed"); } finally { setBusy(false); }
  };
  const s = run.summary;
  const b = run.score_breakdown;

  return (
    <>
      <div className="mb-2 text-sm"><Link href="/research" className="text-muted-foreground hover:underline">Research</Link></div>
      <PageHeader eyebrow={`Research #${run.id}`} title={run.institution.name}
        actions={<>
          <ResearchButton ids={[run.institution.id]} label={run.is_stale ? "Re-run (stale)" : "Re-run research"} variant="outline" onQueued={() => toast("success", "Re-run queued. The new result will replace this one when it finishes.")} />
          <Button onClick={push} disabled={busy || !run.is_latest || toPushF + toPushC === 0}><Send />Push to CRM{toPushF + toPushC ? ` (${toPushF} findings, ${toPushC} contacts)` : ""}</Button>
        </>} />
      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <DemoBadge show={run.institution.is_demo} />
        <RunStatusBadge status={run.status} />
        {!run.is_latest && <Badge variant="warning">A newer result exists — this one is read-only</Badge>}
        {run.is_stale && <Badge variant="warning">Stale — re-run recommended</Badge>}
        {run.pushed_at && <Badge variant="success">Pushed {relative(run.pushed_at)}</Badge>}
        <span>Researched {run.finished_at ? dateTime(run.finished_at) : "—"}</span>
        <span>· {s.documents_reviewed ?? 0} sources · {s.reasoner_model ?? "—"} · ${Number(run.cost_usd).toFixed(4)}</span>
        <Link href={`/institutions/${run.institution.id}`} className="text-primary hover:underline">Institution profile</Link>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="grid min-w-0 grid-cols-1 content-start gap-6 xl:col-span-2">
          <Card>
            <CardContent className="grid gap-5 p-5 sm:grid-cols-[auto_1fr]">
              <div className="flex flex-col items-center justify-center rounded-xl bg-pdpurple-bg px-6 py-4 text-center">
                <div className="font-display text-5xl font-medium tabular text-pdblack">{run.score ?? "—"}</div>
                <div className="text-xs text-muted-foreground">out of 100</div>
                <div className="mt-2 flex items-center gap-1.5 text-sm"><TierBadge tier={run.tier} /> Tier {run.tier}</div>
                <div className="mt-1 text-xs text-muted-foreground">{label(run.confidence)} confidence</div>
              </div>
              <div className="grid gap-3 text-sm">
                <div><div className="eyebrow mb-1">Why it fits</div><p>{s.fit_summary}</p></div>
                <div className="flex gap-2"><Target className="mt-0.5 size-4 shrink-0 text-pdpurple-deep" /><div><span className="font-medium">Most relevant use case: {s.primary_use_case_label ?? "not determined"}.</span> <span className="text-muted-foreground">{s.use_case_rationale}</span></div></div>
                <div className="flex gap-2 rounded-lg border border-pdpurple/30 bg-pdpurple-bg/50 p-3"><Lightbulb className="mt-0.5 size-4 shrink-0 text-pdpurple-deep" /><div><span className="font-medium">Recommended next action: </span>{s.next_action}</div></div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <div><CardTitle>Evidence ({groups.signal.length + groups.profile.length + groups.use_case.length})</CardTitle><CardDescription className="mt-1">Approve what you have checked against the source. Only approved items go to the CRM.</CardDescription></div>
              <Button size="sm" variant="outline" onClick={approveVerified} disabled={!run.is_latest}><CheckCheck />Approve all verified facts</Button>
            </CardHeader>
            <CardContent className="grid gap-5">
              {([["Buying signals and problems", groups.signal], ["Use cases", groups.use_case], ["Institution profile", groups.profile]] as const).map(([title, items]) => items.length > 0 && (
                <section key={title}>
                  <h3 className="mb-2 font-sans text-sm font-semibold">{title}</h3>
                  <ul className="grid gap-2">{items.map((e) => <EvidenceItem key={e.id} e={e} onSet={(st) => review({ evidence: { [e.id]: st } })} />)}</ul>
                </section>
              ))}
              {groups.hypothesis.length > 0 && (
                <section>
                  <h3 className="mb-1 font-sans text-sm font-semibold">Hypotheses — not counted, verify first</h3>
                  <ul className="grid gap-2">{groups.hypothesis.map((e) => <EvidenceItem key={e.id} e={e} onSet={(st) => review({ evidence: { [e.id]: st } })} />)}</ul>
                </section>
              )}
              {run.evidence.length === 0 && <p className="text-sm text-muted-foreground">The research source returned no material for this institution.</p>}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Decision-makers found ({run.candidates.length})</CardTitle><CardDescription className="mt-1">Emails are kept only when published on the cited page or verified by a licensed provider. Guessed addresses are discarded.</CardDescription></CardHeader>
            <CardContent>
              {run.candidates.length === 0 ? <p className="text-sm text-muted-foreground">No relevant people found.</p> : (
                <ul className="grid gap-2">{run.candidates.map((c) => <CandidateRow key={c.id} c={c} onSet={(st) => review({ candidates: { [c.id]: st } })} />)}</ul>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="grid min-w-0 grid-cols-1 content-start gap-6">
          <Card>
            <CardHeader><CardTitle>Score breakdown</CardTitle><CardDescription className="mt-1">Each line shows what earned or withheld points.</CardDescription></CardHeader>
            <CardContent className="grid gap-4">
              {(["fit", "problem", "intent", "access", "quality"] as const).map((k) => b[k] && (
                <div key={k}>
                  <div className="mb-1 flex items-baseline justify-between gap-2 text-sm"><span className="font-medium">{b[k].label}</span><span className="tabular text-muted-foreground"><span className="font-semibold text-foreground">{b[k].points}</span> / {b[k].weight}</span></div>
                  <div className="h-1.5 rounded-full bg-muted" aria-hidden><div className="h-1.5 rounded-full bg-pdpurple" style={{ width: `${b[k].ratio * 100}%` }} /></div>
                  <ul className="mt-1.5 grid gap-0.5 text-xs text-muted-foreground">{b[k].reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
                </div>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><AlertTriangle className="size-4 text-amber-700" />Missing information</CardTitle></CardHeader>
            <CardContent>
              {(s.missing_information ?? []).length === 0 ? <p className="text-sm text-muted-foreground">Nothing important missing.</p> : (
                <ul className="grid gap-2.5 text-sm">{s.missing_information!.map((m) => <li key={m.item}><div className="font-medium">{m.item}</div><div className="text-xs text-muted-foreground">How to verify: {m.how_to_verify}</div></li>)}</ul>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0"><CardTitle>Processing log</CardTitle><Button size="sm" variant="ghost" onClick={() => setShowLog(!showLog)}>{showLog ? "Hide" : "Show"}</Button></CardHeader>
            {showLog && (
              <CardContent>
                <ol className="grid gap-1 font-mono text-[11px] text-muted-foreground">
                  {run.log.map((l, i) => <li key={i}>{l.at.slice(11, 19)} {l.event} {Object.entries(l).filter(([k]) => k !== "at" && k !== "event").map(([k, v]) => `${k}=${v}`).join(" ")}</li>)}
                </ol>
                <div className="mt-2 text-xs text-muted-foreground">{run.provider_calls} source calls · {run.input_tokens + run.output_tokens} AI tokens · attempt {run.attempts}/{run.max_attempts}</div>
              </CardContent>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
