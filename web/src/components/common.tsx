"use client";
import { useEffect, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Search, ShieldOff, Inbox, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { label } from "@/lib/format";
import type { Freshness, LeadStage, OppStage, Priority, Verification } from "@/lib/types";

export function PageHeader({ title, description, actions, eyebrow }: { title: string; description?: string; actions?: ReactNode; eyebrow?: string }) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-1.5">{eyebrow}</div>}
        <h1 className="text-3xl font-medium text-pdblack">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const STAGE_STYLE: Record<LeadStage, string> = {
  new: "bg-muted text-muted-foreground",
  researched: "bg-slate-100 text-slate-700",
  qualified: "bg-pdpurple-bg text-pdpurple-deep",
  outreach_ready: "bg-pdpurple-bg text-pdpurple-deep",
  contacted: "bg-violet-100 text-violet-800",
  engaged: "bg-violet-100 text-violet-800",
  meeting_booked: "bg-indigo-100 text-indigo-800",
  proposal: "bg-indigo-100 text-indigo-800",
  negotiation: "bg-blue-100 text-blue-800",
  won: "bg-emerald-100 text-emerald-800",
  lost: "bg-red-50 text-red-800",
  nurture: "bg-amber-50 text-amber-800",
};

export function StageBadge({ stage }: { stage: LeadStage | OppStage | null | undefined }) {
  if (!stage) return <span className="text-muted-foreground">—</span>;
  const cls = STAGE_STYLE[stage as LeadStage] ?? (stage === "discovery" || stage === "demo" ? STAGE_STYLE.qualified : STAGE_STYLE.new);
  return <span className={cn("inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold whitespace-nowrap", cls)}>{label(stage)}</span>;
}

export function FreshnessBadge({ value }: { value: Freshness }) {
  const map: Record<Freshness, { v: "success" | "warning" | "danger" | "muted"; t: string; title: string }> = {
    fresh: { v: "success", t: "Fresh", title: "Verified in the last 90 days" },
    aging: { v: "warning", t: "Aging", title: "Verified 90–180 days ago" },
    stale: { v: "danger", t: "Stale", title: "Verified over 180 days ago" },
    never: { v: "muted", t: "Unverified", title: "Never verified" },
  };
  const m = map[value];
  return <Badge variant={m.v} title={m.title}>{m.t}</Badge>;
}

export function VerificationBadge({ value }: { value: Verification }) {
  const v = value === "verified" ? "success" : value === "unverified" ? "muted" : "danger";
  return <Badge variant={v}>{label(value)}</Badge>;
}

export function PriorityBadge({ value }: { value: Priority }) {
  const v = value === "urgent" ? "danger" : value === "high" ? "warning" : value === "medium" ? "secondary" : "muted";
  return <Badge variant={v}>{label(value)}</Badge>;
}

export function DemoBadge({ show }: { show: boolean }) {
  return show ? <Badge variant="demo" title="Fictional demo record">DEMO</Badge> : null;
}

export function SuppressedBadge({ show }: { show: boolean }) {
  return show ? <Badge variant="danger" title="Opted out or suppressed — do not contact"><ShieldOff className="size-3" />Do not contact</Badge> : null;
}

export function SearchInput({ value, onChange, placeholder = "Search…" }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  useEffect(() => {
    const t = setTimeout(() => { if (local !== value) onChange(local); }, 300);
    return () => clearTimeout(t);
  }, [local, value, onChange]);
  return (
    <div className="relative w-full sm:w-72">
      <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
      <Input aria-label={placeholder} value={local} onChange={(e) => setLocal(e.target.value)} placeholder={placeholder} className="pl-8" />
    </div>
  );
}

export function SortHeader({ field, sort, onSort, children, className }: { field: string; sort: string; onSort: (s: string) => void; children: ReactNode; className?: string }) {
  const active = sort.replace(/^-/, "") === field;
  const desc = sort.startsWith("-");
  const Icon = !active ? ArrowUpDown : desc ? ArrowDown : ArrowUp;
  return (
    <th className={cn("h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wider text-muted-foreground", className)}
      aria-sort={active ? (desc ? "descending" : "ascending") : "none"}>
      <button className="inline-flex items-center gap-1 uppercase hover:text-foreground" onClick={() => onSort(active && !desc ? `-${field}` : field)}>
        {children}<Icon className={cn("size-3", !active && "opacity-40")} />
      </button>
    </th>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex items-center justify-between gap-2 border-t px-3 py-2.5 text-sm text-muted-foreground">
      <span className="tabular">{from}–{to} of {total.toLocaleString()}</span>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft /></Button>
        <span className="tabular px-1">Page {page} of {pages}</span>
        <Button variant="ghost" size="icon" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}><ChevronRight /></Button>
      </div>
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="mb-1 flex size-10 items-center justify-center rounded-full bg-pdpurple-bg"><Inbox className="size-5 text-pdpurple-deep" /></div>
      <div className="font-medium">{title}</div>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div role="alert" className="flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <AlertTriangle className="size-4 shrink-0" />
      <span className="flex-1">{error instanceof Error ? error.message : "Something went wrong."}</span>
      {retry && <Button size="sm" variant="outline" onClick={retry}>Retry</Button>}
    </div>
  );
}

export function Field({ label: text, htmlFor, children, hint, className }: { label: string; htmlFor: string; children: ReactNode; hint?: string; className?: string }) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{text}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function FilterSelect({ value, onChange, options, allLabel, ariaLabel }: {
  value: string; onChange: (v: string) => void; options: readonly string[] | { value: string; label: string }[]; allLabel: string; ariaLabel: string;
}) {
  const opts = (options as (string | { value: string; label: string })[]).map((o) => (typeof o === "string" ? { value: o, label: label(o) } : o));
  return (
    <select aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-md border border-input bg-background px-2.5 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
      <option value="">{allLabel}</option>
      {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

/** Small state hook for list pages: q, sort, page and arbitrary filters, kept in the URL. */
export function useListState(defaults: Record<string, string> = {}) {
  const read = () => {
    if (typeof window === "undefined") return { ...defaults };
    const sp = new URLSearchParams(window.location.search);
    const o: Record<string, string> = { ...defaults };
    sp.forEach((v, k) => { o[k] = v; });
    return o;
  };
  const [state, setState] = useState<Record<string, string>>(defaults);
  useEffect(() => { setState(read()); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (patch: Record<string, string>) => {
    setState((s) => {
      const next = { ...s, ...patch };
      if (!("page" in patch)) next.page = "1";
      const sp = new URLSearchParams();
      Object.entries(next).forEach(([k, v]) => { if (v && v !== defaults[k]) sp.set(k, v); });
      const q = sp.toString();
      window.history.replaceState(null, "", q ? `?${q}` : window.location.pathname);
      return next;
    });
  };
  return [state, set] as const;
}
