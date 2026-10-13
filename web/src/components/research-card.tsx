"use client";
import Link from "next/link";
import { Lightbulb } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ResearchButton, RunStatusBadge, TierBadge } from "@/components/research";
import { useApi } from "@/lib/api";
import { label, relative } from "@/lib/format";
import type { InstitutionResearch } from "@/lib/types";

/** Latest research result for one institution, with a run / re-run action. Polls while research is in progress. */
export function ResearchCard({ institutionId, onDone }: { institutionId: number; onDone?: () => void }) {
  const { data, mutate } = useApi<InstitutionResearch>(`/research/institutions/${institutionId}`, undefined, {
    refreshInterval: (d) => (d?.latest && ["queued", "running"].includes(d.latest.status) ? 2000 : 0),
    onSuccess: (d) => { if (d.latest?.status === "succeeded" && d.result?.id === d.latest.id) onDone?.(); },
  });
  const r = data?.result;
  const latest = data?.latest;
  const inProgress = latest && (latest.status === "queued" || latest.status === "running");
  const failed = latest && latest.status === "failed" && latest.id !== r?.id;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle>AI research</CardTitle>
        {!inProgress && <ResearchButton ids={[institutionId]} size="sm" variant={r ? "outline" : "default"} label={r ? (r.is_stale ? "Re-run (stale)" : "Re-run") : "Research now"} onQueued={() => mutate()} />}
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {inProgress && (
          <div className="flex items-center gap-2 rounded-md bg-muted p-2.5">
            <RunStatusBadge status={latest.status} />
            <span className="text-muted-foreground">{latest.attempts > 0 && latest.status === "queued" ? `Retrying (attempt ${latest.attempts + 1} of ${latest.max_attempts})` : "Researching cited sources…"}</span>
          </div>
        )}
        {failed && <div className="rounded-md bg-red-50 p-2.5 text-red-800">Last research failed: {latest.last_error}</div>}
        {r ? (
          <>
            <div className="flex items-center gap-3">
              <span className="font-display text-4xl font-medium tabular">{r.score}</span>
              <div className="grid gap-0.5">
                <span className="flex items-center gap-1.5"><TierBadge tier={r.tier} /> Tier {r.tier} · {label(r.confidence)} confidence</span>
                <span className="text-xs text-muted-foreground">Researched {relative(r.finished_at)}{r.is_stale && <Badge variant="warning" className="ml-1.5">Stale</Badge>}</span>
              </div>
            </div>
            {r.summary.primary_use_case_label && <div><span className="text-muted-foreground">Best use case:</span> {r.summary.primary_use_case_label}</div>}
            <div className="flex gap-2 rounded-md bg-pdpurple-bg/60 p-2.5"><Lightbulb className="mt-0.5 size-4 shrink-0 text-pdpurple-deep" /><span>{r.summary.next_action}</span></div>
            <Button asChild variant="secondary" size="sm"><Link href={`/research/runs/${r.id}`}>{r.pushed_at ? "View findings and sources" : "Review findings and sources"}</Link></Button>
          </>
        ) : !inProgress && <p className="text-muted-foreground">Not researched yet. Research finds cited buying signals and decision-makers, and scores fit out of 100.</p>}
      </CardContent>
    </Card>
  );
}
