"use client";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Field } from "@/components/common";
import { useToast } from "@/components/toast";
import { api, qs, useApi } from "@/lib/api";
import { fromLocalInput, label, toLocalInput } from "@/lib/format";
import {
  ACTIVITY_TYPES, INSTITUTION_TYPES, LEAD_STAGES, OPP_STAGES, PRIORITIES, SOLUTIONS, STUDENT_SIZES, VERIFICATION,
  type Campaign, type Contact, type DuplicateMatch, type ImportResult, type Institution, type InstitutionDetail,
  type Lead, type LeadStage, type Opportunity, type Page, type Task, type UserRef,
} from "@/lib/types";

/* ---------- shared bits ---------- */

function useSubmit<T>(fn: () => Promise<T>, onDone: (r: T) => void, success: string) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      toast("success", success);
      onDone(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, submit, setError };
}

function FormDialog({ open, onOpenChange, title, description, children, onSubmit, busy, error, submitLabel = "Save", wide }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; children: ReactNode;
  onSubmit: (e: React.FormEvent) => void; busy: boolean; error: string | null; submitLabel?: string; wide?: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={wide ? "max-w-2xl" : undefined}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <form onSubmit={onSubmit} className="grid gap-4" noValidate>
          {children}
          {error && <p role="alert" className="rounded-md bg-red-50 p-2.5 text-sm text-red-800">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy && <Loader2 className="animate-spin" />}{submitLabel}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function OwnerSelect({ id, value, onChange, allowEmpty = true }: { id: string; value: number | null; onChange: (v: number | null) => void; allowEmpty?: boolean }) {
  const { data } = useApi<UserRef[]>("/users/options");
  return (
    <Select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      {allowEmpty && <option value="">Me (default)</option>}
      {data?.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
    </Select>
  );
}

export function InstitutionPicker({ id, value, onChange }: { id: string; value: { id: number; name: string } | null; onChange: (v: { id: number; name: string } | null) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const { data } = useApi<Page<Institution>>(open ? "/institutions" : null, { q, page_size: 8 });
  if (value) {
    return (
      <div className="flex h-9 items-center justify-between rounded-md border border-input px-3 text-sm">
        <span className="truncate">{value.name}</span>
        <button type="button" className="text-xs text-primary hover:underline" onClick={() => onChange(null)}>Change</button>
      </div>
    );
  }
  return (
    <div className="relative">
      <Input id={id} placeholder="Search institutions…" value={q} autoComplete="off"
        onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)} role="combobox" aria-expanded={open} aria-controls={`${id}-list`} />
      {open && data && (
        <ul id={`${id}-list`} role="listbox" className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-md border bg-popover p-1 shadow-lg">
          {data.items.length === 0 && <li className="px-2 py-1.5 text-sm text-muted-foreground">No matches</li>}
          {data.items.map((i) => (
            <li key={i.id} role="option" aria-selected={false}>
              <button type="button" className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-accent"
                onMouseDown={(e) => e.preventDefault()} onClick={() => { onChange({ id: i.id, name: i.name }); setOpen(false); }}>
                {i.name}<span className="ml-2 text-xs text-muted-foreground">{[i.city, i.country].filter(Boolean).join(", ")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DuplicateWarning({ matches }: { matches: DuplicateMatch[] }) {
  if (!matches.length) return null;
  const exact = matches.some((m) => m.exact);
  return (
    <div className={`flex gap-2 rounded-md p-2.5 text-sm ${exact ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-900"}`} role="status">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div>
        <div className="font-medium">{exact ? "This looks like a duplicate — saving will be blocked." : "Possible matches (review — not merged automatically):"}</div>
        <ul className="mt-1 list-disc pl-4">{matches.map((m) => <li key={m.id}>{m.label} — {m.reason}</li>)}</ul>
      </div>
    </div>
  );
}

/* ---------- institution ---------- */

export function InstitutionDialog({ open, onOpenChange, initial, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; initial?: InstitutionDetail; onSaved: (i: InstitutionDetail) => void;
}) {
  const blank = { name: "", website: "", institution_type: "university", country: "", city: "", student_size_range: "unknown", courses: "", source: "", notes: "", owner_id: null as number | null, verified: false };
  const [f, setF] = useState(blank);
  const [dups, setDups] = useState<DuplicateMatch[]>([]);
  useEffect(() => {
    if (!open) return;
    setDups([]);
    setF(initial ? {
      name: initial.name, website: initial.website ?? "", institution_type: initial.institution_type, country: initial.country,
      city: initial.city, student_size_range: initial.student_size_range, courses: initial.courses.join(", "),
      source: initial.source ?? "", notes: initial.notes ?? "", owner_id: initial.owner?.id ?? null, verified: false,
    } : blank);
  }, [open, initial]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k: keyof typeof blank) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF((s) => ({ ...s, [k]: e.target.value }));

  const checkDups = async () => {
    if (f.name.trim().length < 2) return;
    const r = await api<DuplicateMatch[]>(`/institutions/duplicates${qs({ name: f.name, website: f.website, country: f.country, city: f.city, exclude_id: initial?.id })}`).catch(() => []);
    setDups(r);
  };

  const s = useSubmit(async () => {
    const body = {
      name: f.name, website: f.website || null, institution_type: f.institution_type, country: f.country, city: f.city,
      student_size_range: f.student_size_range, courses: f.courses.split(",").map((c) => c.trim()).filter(Boolean),
      source: f.source || null, notes: f.notes || null, owner_id: f.owner_id,
      ...(f.verified ? { last_verified_at: new Date().toISOString() } : {}),
    };
    return initial
      ? api<InstitutionDetail>(`/institutions/${initial.id}`, { method: "PATCH", json: body })
      : api<InstitutionDetail>("/institutions", { method: "POST", json: body });
  }, (r) => { onSaved(r); onOpenChange(false); }, initial ? "Institution updated" : "Institution created");

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={initial ? "Edit institution" : "New institution"} wide
      description="Website domain and name + location are used to prevent duplicates." onSubmit={s.submit} busy={s.busy} error={s.error}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name *" htmlFor="i-name"><Input id="i-name" required value={f.name} onChange={set("name")} onBlur={checkDups} /></Field>
        <Field label="Website" htmlFor="i-web"><Input id="i-web" type="url" placeholder="https://" value={f.website} onChange={set("website")} onBlur={checkDups} /></Field>
        <Field label="Type" htmlFor="i-type"><Select id="i-type" value={f.institution_type} onChange={set("institution_type")}>{INSTITUTION_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field>
        <Field label="Student size" htmlFor="i-size"><Select id="i-size" value={f.student_size_range} onChange={set("student_size_range")}>{STUDENT_SIZES.map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field>
        <Field label="Country" htmlFor="i-country"><Input id="i-country" value={f.country} onChange={set("country")} onBlur={checkDups} /></Field>
        <Field label="City" htmlFor="i-city"><Input id="i-city" value={f.city} onChange={set("city")} onBlur={checkDups} /></Field>
        <Field label="Courses" htmlFor="i-courses" hint="Comma-separated" className="sm:col-span-2"><Input id="i-courses" value={f.courses} onChange={set("courses")} /></Field>
        <Field label="Source" htmlFor="i-source" hint="Where this record came from"><Input id="i-source" value={f.source} onChange={set("source")} /></Field>
        <Field label="Owner" htmlFor="i-owner"><OwnerSelect id="i-owner" value={f.owner_id} onChange={(v) => setF((s) => ({ ...s, owner_id: v }))} /></Field>
        <Field label="Notes" htmlFor="i-notes" className="sm:col-span-2"><Textarea id="i-notes" value={f.notes} onChange={set("notes")} /></Field>
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.verified} onChange={(e) => setF((s) => ({ ...s, verified: e.target.checked }))} className="accent-[#6D28D9]" />I have verified these details today</label>
      <DuplicateWarning matches={dups} />
    </FormDialog>
  );
}

/* ---------- contact ---------- */

export function ContactDialog({ open, onOpenChange, initial, institution, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; initial?: Contact; institution?: { id: number; name: string }; onSaved: (c: Contact) => void;
}) {
  const blank = { first_name: "", last_name: "", designation: "", department: "", email: "", phone: "", linkedin_url: "", verification_status: "unverified", source: "", verified: false };
  const [f, setF] = useState(blank);
  const [inst, setInst] = useState<{ id: number; name: string } | null>(null);
  const [dups, setDups] = useState<DuplicateMatch[]>([]);
  useEffect(() => {
    if (!open) return;
    setDups([]);
    setInst(initial ? initial.institution : institution ?? null);
    setF(initial ? {
      first_name: initial.first_name, last_name: initial.last_name, designation: initial.designation ?? "", department: initial.department ?? "",
      email: initial.email ?? "", phone: initial.phone ?? "", linkedin_url: initial.linkedin_url ?? "",
      verification_status: initial.verification_status, source: initial.source ?? "", verified: false,
    } : blank);
  }, [open, initial, institution]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k: keyof typeof blank) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF((s) => ({ ...s, [k]: e.target.value }));
  const checkDups = async () => {
    if (!f.first_name && !f.email) return;
    const r = await api<DuplicateMatch[]>(`/contacts/duplicates${qs({ first_name: f.first_name, last_name: f.last_name, institution_id: inst?.id, email: f.email, linkedin_url: f.linkedin_url, exclude_id: initial?.id })}`).catch(() => []);
    setDups(r);
  };
  const s = useSubmit(async () => {
    if (!inst) throw new Error("Choose an institution");
    const body = {
      institution_id: inst.id, first_name: f.first_name, last_name: f.last_name, designation: f.designation, department: f.department,
      email: f.email, phone: f.phone, linkedin_url: f.linkedin_url, verification_status: f.verification_status, source: f.source,
      ...(f.verified ? { last_verified_at: new Date().toISOString() } : {}),
    };
    return initial ? api<Contact>(`/contacts/${initial.id}`, { method: "PATCH", json: body }) : api<Contact>("/contacts", { method: "POST", json: body });
  }, (r) => { onSaved(r); onOpenChange(false); }, initial ? "Contact updated" : "Contact created");

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={initial ? "Edit contact" : "New contact"} wide onSubmit={s.submit} busy={s.busy} error={s.error}
      description="Use professional contact details only. Add a LinkedIn URL only where lawfully available.">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Institution *" htmlFor="c-inst" className="sm:col-span-2"><InstitutionPicker id="c-inst" value={inst} onChange={setInst} /></Field>
        <Field label="First name *" htmlFor="c-first"><Input id="c-first" value={f.first_name} onChange={set("first_name")} onBlur={checkDups} /></Field>
        <Field label="Last name" htmlFor="c-last"><Input id="c-last" value={f.last_name} onChange={set("last_name")} onBlur={checkDups} /></Field>
        <Field label="Designation" htmlFor="c-desig"><Input id="c-desig" value={f.designation} onChange={set("designation")} /></Field>
        <Field label="Department" htmlFor="c-dept"><Input id="c-dept" value={f.department} onChange={set("department")} /></Field>
        <Field label="Work email" htmlFor="c-email"><Input id="c-email" type="email" value={f.email} onChange={set("email")} onBlur={checkDups} /></Field>
        <Field label="Work phone" htmlFor="c-phone"><Input id="c-phone" value={f.phone} onChange={set("phone")} /></Field>
        <Field label="LinkedIn URL" htmlFor="c-li"><Input id="c-li" placeholder="https://linkedin.com/in/…" value={f.linkedin_url} onChange={set("linkedin_url")} onBlur={checkDups} /></Field>
        <Field label="Verification" htmlFor="c-ver"><Select id="c-ver" value={f.verification_status} onChange={set("verification_status")}>{VERIFICATION.map((v) => <option key={v} value={v}>{label(v)}</option>)}</Select></Field>
        <Field label="Source" htmlFor="c-src" className="sm:col-span-2"><Input id="c-src" value={f.source} onChange={set("source")} /></Field>
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.verified} onChange={(e) => setF((s) => ({ ...s, verified: e.target.checked }))} className="accent-[#6D28D9]" />I have verified these details today</label>
      <DuplicateWarning matches={dups} />
    </FormDialog>
  );
}

/* ---------- lead ---------- */

export function LeadDialog({ open, onOpenChange, institution, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; institution?: { id: number; name: string }; onSaved: (l: Lead) => void;
}) {
  const [inst, setInst] = useState<{ id: number; name: string } | null>(null);
  const [f, setF] = useState({ primary_contact_id: "", stage: "new", score: "50", source: "", summary: "", owner_id: null as number | null, campaign_id: "" });
  useEffect(() => { if (open) { setInst(institution ?? null); setF({ primary_contact_id: "", stage: "new", score: "50", source: "", summary: "", owner_id: null, campaign_id: "" }); } }, [open, institution]);
  const { data: contacts } = useApi<Page<Contact>>(inst ? "/contacts" : null, { institution_id: inst?.id, page_size: 100 });
  const { data: campaigns } = useApi<Campaign[]>(open ? "/campaigns" : null);
  const s = useSubmit(async () => {
    if (!inst) throw new Error("Choose an institution");
    return api<Lead>("/leads", { method: "POST", json: {
      institution_id: inst.id, primary_contact_id: f.primary_contact_id ? Number(f.primary_contact_id) : null, stage: f.stage,
      score: Number(f.score) || 0, source: f.source || null, summary: f.summary || null, owner_id: f.owner_id,
      campaign_id: f.campaign_id ? Number(f.campaign_id) : null,
    } });
  }, (r) => { onSaved(r); onOpenChange(false); }, "Lead created");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="New lead" description="One open lead per institution." onSubmit={s.submit} busy={s.busy} error={s.error}>
      <Field label="Institution *" htmlFor="l-inst"><InstitutionPicker id="l-inst" value={inst} onChange={setInst} /></Field>
      <Field label="Primary contact" htmlFor="l-contact">
        <Select id="l-contact" value={f.primary_contact_id} onChange={(e) => setF({ ...f, primary_contact_id: e.target.value })} disabled={!inst}>
          <option value="">— None yet —</option>
          {contacts?.items.map((c) => <option key={c.id} value={c.id} disabled={c.suppressed}>{c.full_name}{c.designation ? ` · ${c.designation}` : ""}{c.suppressed ? " (do not contact)" : ""}</option>)}
        </Select>
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Stage" htmlFor="l-stage"><Select id="l-stage" value={f.stage} onChange={(e) => setF({ ...f, stage: e.target.value })}>{LEAD_STAGES.filter((x) => x !== "won" && x !== "lost").map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
        <Field label="Score (0–100)" htmlFor="l-score"><Input id="l-score" type="number" min={0} max={100} value={f.score} onChange={(e) => setF({ ...f, score: e.target.value })} /></Field>
        <Field label="Owner" htmlFor="l-owner"><OwnerSelect id="l-owner" value={f.owner_id} onChange={(v) => setF({ ...f, owner_id: v })} /></Field>
        <Field label="Campaign" htmlFor="l-camp"><Select id="l-camp" value={f.campaign_id} onChange={(e) => setF({ ...f, campaign_id: e.target.value })}><option value="">None</option>{campaigns?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
      </div>
      <Field label="Source" htmlFor="l-src"><Input id="l-src" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} /></Field>
      <Field label="Summary" htmlFor="l-sum"><Textarea id="l-sum" value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} /></Field>
    </FormDialog>
  );
}

export function StageDialog({ open, onOpenChange, lead, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; lead: Lead; onSaved: (l: Lead) => void }) {
  const [stage, setStage] = useState<LeadStage>(lead.stage);
  const [note, setNote] = useState("");
  const [lost, setLost] = useState("");
  useEffect(() => { if (open) { setStage(lead.stage); setNote(""); setLost(""); } }, [open, lead.stage]);
  const s = useSubmit(() => api<Lead>(`/leads/${lead.id}/stage`, { method: "POST", json: { stage, note: note || null, lost_reason: lost || null } }),
    (r) => { onSaved(r); onOpenChange(false); }, "Stage updated");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Move lead" description={lead.institution.name} onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel="Move">
      <Field label="New stage" htmlFor="s-stage"><Select id="s-stage" value={stage} onChange={(e) => setStage(e.target.value as LeadStage)}>{LEAD_STAGES.map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
      {stage === "lost" && <Field label="Lost reason *" htmlFor="s-lost"><Input id="s-lost" value={lost} onChange={(e) => setLost(e.target.value)} /></Field>}
      <Field label="Note for the timeline" htmlFor="s-note"><Textarea id="s-note" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
    </FormDialog>
  );
}

/* ---------- opportunity ---------- */

export function OpportunityDialog({ open, onOpenChange, lead, initial, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; lead?: Lead; initial?: Opportunity; onSaved: (o: Opportunity) => void;
}) {
  const blank = { name: "", estimated_value: "", currency: "USD", solution_interest: [] as string[], expected_decision_date: "", owner_id: null as number | null, stage: "discovery", next_action: "", next_action_date: "" };
  const [f, setF] = useState(blank);
  useEffect(() => {
    if (!open) return;
    setF(initial ? {
      name: initial.name, estimated_value: initial.estimated_value, currency: initial.currency, solution_interest: initial.solution_interest,
      expected_decision_date: initial.expected_decision_date ?? "", owner_id: initial.owner?.id ?? null, stage: initial.stage,
      next_action: initial.next_action ?? "", next_action_date: initial.next_action_date ?? "",
    } : { ...blank, name: lead ? `${lead.institution.name} — Voice AI + CRM` : "" });
  }, [open, initial, lead]); // eslint-disable-line react-hooks/exhaustive-deps
  const s = useSubmit(() => {
    const body = {
      name: f.name, estimated_value: f.estimated_value || "0", currency: f.currency.toUpperCase(), solution_interest: f.solution_interest,
      expected_decision_date: f.expected_decision_date || null, owner_id: f.owner_id, stage: f.stage,
      next_action: f.next_action || null, next_action_date: f.next_action_date || null,
    };
    return initial
      ? api<Opportunity>(`/opportunities/${initial.id}`, { method: "PATCH", json: body })
      : api<Opportunity>("/opportunities", { method: "POST", json: { ...body, lead_id: lead!.id } });
  }, (r) => { onSaved(r); onOpenChange(false); }, initial ? "Opportunity updated" : "Opportunity created");
  const toggle = (v: string) => setF((s) => ({ ...s, solution_interest: s.solution_interest.includes(v) ? s.solution_interest.filter((x) => x !== v) : [...s.solution_interest, v] }));
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={initial ? "Edit opportunity" : "New opportunity"} wide onSubmit={s.submit} busy={s.busy} error={s.error}>
      <Field label="Name *" htmlFor="o-name"><Input id="o-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Estimated value" htmlFor="o-val"><Input id="o-val" type="number" min={0} step="0.01" value={f.estimated_value} onChange={(e) => setF({ ...f, estimated_value: e.target.value })} /></Field>
        <Field label="Currency" htmlFor="o-cur"><Input id="o-cur" maxLength={3} value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Stage" htmlFor="o-stage"><Select id="o-stage" value={f.stage} onChange={(e) => setF({ ...f, stage: e.target.value })}>{OPP_STAGES.map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
        <Field label="Expected decision" htmlFor="o-dec"><Input id="o-dec" type="date" value={f.expected_decision_date} onChange={(e) => setF({ ...f, expected_decision_date: e.target.value })} /></Field>
        <Field label="Owner" htmlFor="o-owner" className="sm:col-span-2"><OwnerSelect id="o-owner" value={f.owner_id} onChange={(v) => setF({ ...f, owner_id: v })} /></Field>
      </div>
      <fieldset className="grid gap-2">
        <legend className="mb-1.5 text-sm font-medium">Solution interest</legend>
        <div className="flex flex-wrap gap-2">
          {SOLUTIONS.map((x) => (
            <label key={x} className={`cursor-pointer rounded-full border px-3 py-1 text-sm ${f.solution_interest.includes(x) ? "border-primary bg-pdpurple-bg text-pdpurple-deep" : ""}`}>
              <input type="checkbox" className="sr-only" checked={f.solution_interest.includes(x)} onChange={() => toggle(x)} />{label(x)}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Next action" htmlFor="o-next" className="sm:col-span-2"><Input id="o-next" value={f.next_action} onChange={(e) => setF({ ...f, next_action: e.target.value })} /></Field>
        <Field label="Next action date" htmlFor="o-nextd"><Input id="o-nextd" type="date" value={f.next_action_date} onChange={(e) => setF({ ...f, next_action_date: e.target.value })} /></Field>
      </div>
    </FormDialog>
  );
}

/* ---------- task ---------- */

export function TaskDialog({ open, onOpenChange, initial, link, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; initial?: Task; link?: { lead_id?: number; institution_id?: number; contact_id?: number; opportunity_id?: number }; onSaved: (t: Task) => void;
}) {
  const blank = { title: "", description: "", due_at: "", reminder_at: "", priority: "medium", owner_id: null as number | null };
  const [f, setF] = useState(blank);
  useEffect(() => {
    if (!open) return;
    setF(initial ? { title: initial.title, description: initial.description ?? "", due_at: toLocalInput(initial.due_at), reminder_at: toLocalInput(initial.reminder_at), priority: initial.priority, owner_id: initial.owner?.id ?? null } : blank);
  }, [open, initial]); // eslint-disable-line react-hooks/exhaustive-deps
  const s = useSubmit(() => {
    const body = { title: f.title, description: f.description || null, due_at: fromLocalInput(f.due_at), reminder_at: fromLocalInput(f.reminder_at), priority: f.priority, owner_id: f.owner_id };
    return initial ? api<Task>(`/tasks/${initial.id}`, { method: "PATCH", json: body }) : api<Task>("/tasks", { method: "POST", json: { ...body, ...link } });
  }, (r) => { onSaved(r); onOpenChange(false); }, initial ? "Task updated" : "Task created");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={initial ? "Edit task" : "New task"} onSubmit={s.submit} busy={s.busy} error={s.error}>
      <Field label="Title *" htmlFor="t-title"><Input id="t-title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Due" htmlFor="t-due"><Input id="t-due" type="datetime-local" value={f.due_at} onChange={(e) => setF({ ...f, due_at: e.target.value })} /></Field>
        <Field label="Reminder" htmlFor="t-rem"><Input id="t-rem" type="datetime-local" value={f.reminder_at} onChange={(e) => setF({ ...f, reminder_at: e.target.value })} /></Field>
        <Field label="Priority" htmlFor="t-pri"><Select id="t-pri" value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>{PRIORITIES.map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
        <Field label="Owner" htmlFor="t-owner"><OwnerSelect id="t-owner" value={f.owner_id} onChange={(v) => setF({ ...f, owner_id: v })} /></Field>
      </div>
      <Field label="Details" htmlFor="t-desc"><Textarea id="t-desc" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
    </FormDialog>
  );
}

/* ---------- activity & research ---------- */

export function ActivityDialog({ open, onOpenChange, leadId, institutionId, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; leadId?: number; institutionId: number; onSaved: () => void;
}) {
  const [f, setF] = useState({ activity_type: "call", subject: "", body: "", occurred_at: "", contact_id: "" });
  const [openedAt, setOpenedAt] = useState("");
  useEffect(() => {
    if (!open) return;
    const now = toLocalInput(new Date().toISOString());
    setOpenedAt(now);
    setF({ activity_type: "call", subject: "", body: "", occurred_at: now, contact_id: "" });
  }, [open]);
  const { data: contacts } = useApi<Page<Contact>>(open ? "/contacts" : null, { institution_id: institutionId, page_size: 100 });
  const s = useSubmit(() => api("/activities", { method: "POST", json: {
    activity_type: f.activity_type, subject: f.subject, body: f.body || null,
    // Untouched time field = "now" on the server (keeps exact ordering on the timeline).
    occurred_at: f.occurred_at === openedAt ? null : fromLocalInput(f.occurred_at),
    lead_id: leadId ?? null, institution_id: institutionId, contact_id: f.contact_id ? Number(f.contact_id) : null,
  } }), () => { onSaved(); onOpenChange(false); }, "Activity logged");
  const types = ACTIVITY_TYPES.filter((t) => t !== "status_change" && t !== "research");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Log activity" onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel="Log">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Type" htmlFor="a-type"><Select id="a-type" value={f.activity_type} onChange={(e) => setF({ ...f, activity_type: e.target.value })}>{types.map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
        <Field label="When" htmlFor="a-when"><Input id="a-when" type="datetime-local" value={f.occurred_at} onChange={(e) => setF({ ...f, occurred_at: e.target.value })} /></Field>
      </div>
      <Field label="Contact" htmlFor="a-contact">
        <Select id="a-contact" value={f.contact_id} onChange={(e) => setF({ ...f, contact_id: e.target.value })}>
          <option value="">— None —</option>
          {contacts?.items.map((c) => <option key={c.id} value={c.id}>{c.full_name}{c.suppressed ? " (do not contact)" : ""}</option>)}
        </Select>
      </Field>
      <Field label="Subject *" htmlFor="a-subj"><Input id="a-subj" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} /></Field>
      <Field label="Details" htmlFor="a-body"><Textarea id="a-body" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></Field>
    </FormDialog>
  );
}

export function ResearchDialog({ open, onOpenChange, institutionId, leadId, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; institutionId: number; leadId?: number; onSaved: () => void;
}) {
  const [f, setF] = useState({ category: "", summary: "", source_url: "", confidence: "60" });
  useEffect(() => { if (open) setF({ category: "", summary: "", source_url: "", confidence: "60" }); }, [open]);
  const s = useSubmit(() => api("/research", { method: "POST", json: {
    institution_id: institutionId, lead_id: leadId ?? null, category: f.category, summary: f.summary, source_url: f.source_url || null, confidence: Number(f.confidence),
  } }), () => { onSaved(); onOpenChange(false); }, "Research finding added");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Add research finding" onSubmit={s.submit} busy={s.busy} error={s.error}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Category *" htmlFor="r-cat"><Input id="r-cat" placeholder="e.g. Pain point" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} /></Field>
        <Field label="Confidence (0–100)" htmlFor="r-conf"><Input id="r-conf" type="number" min={0} max={100} value={f.confidence} onChange={(e) => setF({ ...f, confidence: e.target.value })} /></Field>
      </div>
      <Field label="Finding *" htmlFor="r-sum"><Textarea id="r-sum" value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} /></Field>
      <Field label="Source URL" htmlFor="r-url"><Input id="r-url" type="url" value={f.source_url} onChange={(e) => setF({ ...f, source_url: e.target.value })} /></Field>
    </FormDialog>
  );
}

/* ---------- consent ---------- */

export function ConsentDialog({ open, onOpenChange, contact, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; contact?: Contact; onSaved: () => void }) {
  const [f, setF] = useState({ email: "", domain: "", channel: "all", status: "opted_out", reason: "" });
  useEffect(() => { if (open) setF({ email: contact?.email ?? "", domain: "", channel: "all", status: "opted_out", reason: "" }); }, [open, contact]);
  const s = useSubmit(() => api("/consent", { method: "POST", json: {
    contact_id: contact?.id ?? null, email: f.email || null, domain: f.domain || null, channel: f.channel, status: f.status, reason: f.reason || null, source: "Manual entry",
  } }), () => { onSaved(); onOpenChange(false); }, "Recorded");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={contact ? `Opt out ${contact.full_name}` : "Add opt-out / suppression"} onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel="Record"
      description="Opted-out and suppressed contacts are blocked from outreach stages, calls and emails.">
      {!contact && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Email" htmlFor="cs-email"><Input id="cs-email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Or whole domain" htmlFor="cs-dom" hint="e.g. example.edu"><Input id="cs-dom" value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value.toLowerCase() })} /></Field>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Status" htmlFor="cs-status"><Select id="cs-status" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="opted_out">Opted out</option><option value="suppressed">Suppressed</option><option value="consented">Consented (managers)</option></Select></Field>
        <Field label="Channel" htmlFor="cs-ch"><Select id="cs-ch" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{["all", "email", "call", "whatsapp", "sms"].map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
      </div>
      <Field label="Reason" htmlFor="cs-reason"><Input id="cs-reason" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
    </FormDialog>
  );
}

/* ---------- CSV import ---------- */

export function ImportDialog({ open, onOpenChange, entity, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; entity: "institutions" | "contacts"; onDone: () => void }) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (open) { setFile(null); setResult(null); setError(null); } }, [open]);
  const run = async (dry: boolean) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api<ImportResult>(`/${entity}/import?dry_run=${dry}`, { method: "POST", body: fd });
      setResult(r);
      if (!dry) { toast("success", `Imported ${r.created} ${entity}`); onDone(); }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setBusy(false);
    }
  };
  const columns = entity === "institutions"
    ? "name, website, institution_type, country, city, student_size_range, courses (separated by ;), source"
    : "institution_website or institution, first_name, last_name, designation, department, email, phone, linkedin_url, verification_status, source";
  const problems = useMemo(() => result?.rows.filter((r) => r.status !== "created") ?? [], [result]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import {entity} from CSV</DialogTitle>
          <DialogDescription>Columns: {columns}. Check first — nothing is saved until you confirm. Duplicates are skipped.</DialogDescription>
        </DialogHeader>
        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-6 text-sm hover:bg-muted/50">
          <Upload className="size-5 text-muted-foreground" />
          <span>{file ? file.name : "Choose a UTF-8 CSV file (max 5 MB, 5,000 rows)"}</span>
          <input type="file" accept=".csv,text/csv" className="sr-only" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} />
        </label>
        {error && <p role="alert" className="rounded-md bg-red-50 p-2.5 text-sm text-red-800">{error}</p>}
        {result && (
          <div className="grid gap-2 text-sm">
            <div className="flex flex-wrap gap-3">
              <span className="rounded-md bg-emerald-50 px-2 py-1 text-emerald-800">{result.created} {result.dry_run ? "ready to import" : "imported"}</span>
              <span className="rounded-md bg-amber-50 px-2 py-1 text-amber-900">{result.duplicates} duplicates skipped</span>
              <span className="rounded-md bg-red-50 px-2 py-1 text-red-800">{result.errors} errors</span>
            </div>
            {problems.length > 0 && (
              <ul className="max-h-48 overflow-auto rounded-md border p-2 text-xs">
                {problems.map((r) => <li key={r.row} className="py-0.5"><span className="font-mono">Row {r.row}</span> · {label(r.status)} · {r.message}</li>)}
              </ul>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{result && !result.dry_run ? "Close" : "Cancel"}</Button>
          {(!result || result.dry_run) && <Button variant="secondary" disabled={!file || busy} onClick={() => run(true)}>{busy && <Loader2 className="animate-spin" />}Check file</Button>}
          {result?.dry_run && result.created > 0 && <Button disabled={busy} onClick={() => run(false)}>Import {result.created}</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
