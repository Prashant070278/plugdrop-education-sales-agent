"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, CircleSlash, Info, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/input";
import { Field } from "@/components/common";
import { FormDialog, useSubmit } from "@/components/forms";
import { api, useApi } from "@/lib/api";
import { label } from "@/lib/format";
import { USE_CASE_OPTIONS, type EligibleLead, type Mailbox, type MessageStatus, type OutreachSequenceDetail, type Quality, type ReplyLabel } from "@/lib/types";
import { cn } from "@/lib/utils";

type V = "default" | "secondary" | "outline" | "success" | "warning" | "danger" | "muted";

const MSG: Record<MessageStatus, [string, V]> = {
  draft: ["Draft", "muted"],
  approved: ["Approved", "secondary"],
  scheduled: ["Scheduled", "secondary"],
  sending: ["Sending…", "warning"],
  drafted_in_mailbox: ["In mailbox as draft", "secondary"],
  sent: ["Sent", "success"],
  delivered: ["Delivered", "success"],
  bounced: ["Bounced", "danger"],
  failed: ["Failed", "danger"],
  cancelled: ["Cancelled", "muted"],
  blocked: ["Held back", "warning"],
};
export function MessageStatusBadge({ status }: { status: MessageStatus }) {
  const [t, v] = MSG[status] ?? [label(status), "muted"];
  return <Badge variant={v}>{t}</Badge>;
}

const REPLY: Record<ReplyLabel, [string, V]> = {
  interested: ["Interested", "success"],
  meeting_request: ["Meeting request", "success"],
  needs_information: ["Needs information", "secondary"],
  not_now: ["Not now", "warning"],
  not_interested: ["Not interested", "danger"],
  referral: ["Referral", "secondary"],
  out_of_office: ["Out of office / auto-reply", "muted"],
  unclear: ["Unclear", "muted"],
};
export function ReplyLabelBadge({ value, optOut }: { value: ReplyLabel; optOut?: boolean }) {
  const [t, v] = REPLY[value] ?? [label(value), "muted"];
  return <>{optOut && <Badge variant="danger">Opted out</Badge>} <Badge variant={v}>{t}</Badge></>;
}
export const replyLabelText = (v: ReplyLabel) => REPLY[v]?.[0] ?? label(v);

export function SequenceStatusBadge({ status, reason }: { status: string; reason?: string | null }) {
  const v: V = status === "active" ? "success" : status === "draft" ? "muted" : status === "completed" ? "secondary" : "warning";
  return <Badge variant={v}>{label(status)}{reason ? `: ${label(reason)}` : ""}</Badge>;
}

export function QualityChecks({ quality, compact }: { quality: Quality; compact?: boolean }) {
  const checks = quality.checks ?? [];
  const failed = checks.filter((c) => !c.passed);
  if (compact) {
    if (!checks.length) return null;
    return quality.passed
      ? <span className="inline-flex items-center gap-1 text-xs text-emerald-800"><CheckCircle2 className="size-3.5" />Checks passed{failed.length ? ` · ${failed.length} note${failed.length > 1 ? "s" : ""}` : ""}</span>
      : <span className="inline-flex items-center gap-1 text-xs text-red-800"><XCircle className="size-3.5" />{failed.filter((c) => c.blocking).length} check(s) to fix</span>;
  }
  return (
    <ul className="grid gap-1 text-xs" aria-label="Quality checks">
      {checks.map((c) => (
        <li key={c.id} className={cn("flex items-start gap-1.5", c.passed ? "text-muted-foreground" : c.blocking ? "text-red-800" : "text-amber-800")}>
          {c.passed ? <CheckCircle2 className="mt-px size-3.5 shrink-0 text-emerald-700" /> : c.blocking ? <XCircle className="mt-px size-3.5 shrink-0" /> : <AlertTriangle className="mt-px size-3.5 shrink-0" />}
          <span><span className={cn(!c.passed && "font-medium")}>{c.label}</span>{c.detail ? ` — ${c.detail}` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

export function ModeNotice({ mode, reason }: { mode: string; reason?: string }) {
  if (mode === "send") return <Badge variant="success" title={reason}>Sends automatically</Badge>;
  if (mode === "none") return <Badge variant="danger" title={reason}><CircleSlash className="size-3" />Can&apos;t send</Badge>;
  return <Badge variant="secondary" title={reason}><Info className="size-3" />Draft-only: lands in the mailbox for a person to send</Badge>;
}

/** Choose an eligible prospect + contact + mailbox and generate a sequence. */
export function StartOutreachDialog({ open, onOpenChange, leadId }: { open: boolean; onOpenChange: (o: boolean) => void; leadId?: number }) {
  const router = useRouter();
  const { data: leads } = useApi<EligibleLead[]>(open ? "/outreach/eligible" : null, { limit: 200 });
  const { data: mailboxes } = useApi<Mailbox[]>(open ? "/outreach/mailboxes" : null);
  const [lead, setLead] = useState<string>("");
  const [contact, setContact] = useState<string>("");
  const [mailbox, setMailbox] = useState<string>("");
  const [useCase, setUseCase] = useState<string>("");
  useEffect(() => {
    if (!open) return;
    setLead(leadId ? String(leadId) : ""); setContact(""); setUseCase("");
  }, [open, leadId]);
  const active = (mailboxes ?? []).filter((m) => m.status === "active");
  useEffect(() => { if (!mailbox && active.length) setMailbox(String(active[0].id)); }, [active, mailbox]);
  const sel = leads?.find((l) => String(l.lead_id) === lead);
  const ready = sel?.contacts.filter((c) => c.problems.length === 0) ?? [];
  const blocked = sel?.contacts.filter((c) => c.problems.length > 0) ?? [];
  const s = useSubmit(async () => {
    if (!sel) throw new Error("Choose a prospect");
    if (!contact) throw new Error("Choose a contact");
    if (!mailbox) throw new Error("Choose a mailbox (an admin can connect one in Settings → Outreach)");
    return api<OutreachSequenceDetail>("/outreach/sequences", { method: "POST", json: { lead_id: sel.lead_id, contact_id: Number(contact), mailbox_id: Number(mailbox), use_case: useCase || null } });
  }, (r) => { onOpenChange(false); router.push(`/outreach/sequences/${r.id}`); }, "Emails drafted — review and approve them");
  const leadMissing = leadId && leads && !sel;
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Start outreach" wide onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel="Draft emails"
      description="Drafts a first email and follow-ups from approved research only. Nothing is sent until you approve it.">
      {leadMissing && <p className="rounded-md bg-amber-50 p-2.5 text-sm text-amber-900">This lead isn&apos;t ready for outreach yet: its research must be reviewed and pushed to the CRM first.</p>}
      <Field label="Prospect (approved research only)" htmlFor="so-lead">
        <Select id="so-lead" value={lead} onChange={(e) => { setLead(e.target.value); setContact(""); }} disabled={!!leadId && !!sel}>
          <option value="">{leads ? (leads.length ? "Choose…" : "No prospects with approved research yet") : "Loading…"}</option>
          {leads?.map((l) => <option key={l.lead_id} value={l.lead_id}>{l.institution.name}{l.institution.score != null ? ` — score ${l.institution.score} (tier ${l.institution.tier})` : ""}</option>)}
        </Select>
      </Field>
      {sel && (
        <Field label="Contact" htmlFor="so-contact">
          <Select id="so-contact" value={contact} onChange={(e) => setContact(e.target.value)}>
            <option value="">{ready.length ? "Choose…" : "No contact is ready — see below"}</option>
            {ready.map((c) => <option key={c.id} value={c.id}>{c.name}{c.designation ? ` · ${c.designation}` : ""} · {c.email}</option>)}
          </Select>
          {blocked.length > 0 && (
            <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">{blocked.length} contact(s) can&apos;t be emailed</summary>
              <ul className="mt-1 grid gap-1">{blocked.map((c) => <li key={c.id}><span className="font-medium text-foreground">{c.name}</span>: {c.problems.join("; ")}</li>)}</ul>
            </details>
          )}
        </Field>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Send from" htmlFor="so-mb">
          <Select id="so-mb" value={mailbox} onChange={(e) => setMailbox(e.target.value)}>
            {!active.length && <option value="">No approved mailbox</option>}
            {active.map((m) => <option key={m.id} value={m.id}>{m.display_name} &lt;{m.email_address}&gt;{m.provider === "mock" ? " (test)" : ""}</option>)}
          </Select>
        </Field>
        <Field label="Use case" htmlFor="so-uc">
          <Select id="so-uc" value={useCase} onChange={(e) => setUseCase(e.target.value)}>
            <option value="">Best match from research</option>
            {USE_CASE_OPTIONS.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
          </Select>
        </Field>
      </div>
    </FormDialog>
  );
}
