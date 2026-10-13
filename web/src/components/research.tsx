"use client";
import { useState } from "react";
import { Loader2, Microscope } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, type ButtonProps } from "@/components/ui/button";
import { useToast } from "@/components/toast";
import { api } from "@/lib/api";
import type { EvidenceLevel, ResearchJob, RunStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const TIER_STYLE: Record<string, string> = {
  A: "bg-pdpurple-deep text-white",
  B: "bg-pdpurple-bg text-pdpurple-deep ring-1 ring-inset ring-pdpurple/30",
  C: "bg-muted text-foreground",
  D: "bg-muted text-muted-foreground",
};

export function TierBadge({ tier, className }: { tier: string | null | undefined; className?: string }) {
  if (!tier) return <span className="text-muted-foreground">—</span>;
  return (
    <span title={`Tier ${tier}`} className={cn("inline-flex size-6 items-center justify-center rounded-md text-xs font-bold", TIER_STYLE[tier], className)}>
      {tier}
    </span>
  );
}

export function ScorePill({ score, tier }: { score: number | null | undefined; tier?: string | null }) {
  if (score === null || score === undefined) return <span className="text-sm text-muted-foreground">Not researched</span>;
  return (
    <span className="inline-flex items-center gap-2">
      <TierBadge tier={tier} />
      <span className="w-14 overflow-hidden rounded-full bg-muted" aria-hidden><span className="block h-1.5 rounded-full bg-pdpurple" style={{ width: `${score}%` }} /></span>
      <span className="text-sm font-medium tabular">{score}</span>
    </span>
  );
}

const LEVEL: Record<EvidenceLevel, { label: string; variant: "success" | "secondary" | "warning"; title: string }> = {
  verified_fact: { label: "Verified fact", variant: "success", title: "Stated directly in the cited source; the quote was checked against the source text" },
  source_inference: { label: "Source-supported inference", variant: "secondary", title: "A reasonable inference from the cited source, not stated outright" },
  hypothesis: { label: "Hypothesis", variant: "warning", title: "Not supported by a source. Never counted in the score; verify before using" },
};

export function EvidenceLevelBadge({ level }: { level: EvidenceLevel }) {
  const l = LEVEL[level];
  return <Badge variant={l.variant} title={l.title}>{l.label}</Badge>;
}

export function RunStatusBadge({ status }: { status: RunStatus | ResearchJob["status"] }) {
  const map: Record<string, "success" | "danger" | "warning" | "muted" | "secondary"> = {
    succeeded: "success", completed: "success", failed: "danger", partial: "warning", cancelled: "muted",
    queued: "secondary", running: "secondary",
  };
  return (
    <Badge variant={map[status] ?? "muted"}>
      {(status === "running" || status === "queued") && <Loader2 className={cn("size-3", status === "running" && "animate-spin")} />}
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  );
}

export function ResearchButton({ ids, label, onQueued, ...props }: { ids: number[]; label?: string; onQueued?: (j: ResearchJob) => void } & ButtonProps) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const job = await api<ResearchJob>("/research/jobs", { method: "POST", json: { institution_ids: ids } });
      toast("success", ids.length === 1 ? "Research queued" : `Research queued for ${job.total_items} institutions`);
      onQueued?.(job);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Could not start research");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button onClick={run} disabled={busy || ids.length === 0} {...props}>
      {busy ? <Loader2 className="animate-spin" /> : <Microscope />}{label ?? "Research"}
    </Button>
  );
}
