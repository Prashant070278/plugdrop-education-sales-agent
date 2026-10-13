"use client";
import { useEffect, useState } from "react";
import { Check, Mail, Plus, Save, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { DemoBadge, EmptyState, Field } from "@/components/common";
import { FormDialog, useSubmit } from "@/components/forms";
import { ModeNotice } from "@/components/outreach";
import { useCan } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, qs, useApi } from "@/lib/api";
import { label, relative } from "@/lib/format";
import { USE_CASE_OPTIONS, type ContentItem, type EmailTemplateT, type Mailbox, type MailProvider, type OutreachPolicy } from "@/lib/types";

const ucLabel = (v: string) => USE_CASE_OPTIONS.find((u) => u.value === v)?.label ?? label(v);

function Policy() {
  const toast = useToast();
  const isAdmin = useCan("admin");
  const { data, mutate } = useApi<OutreachPolicy>("/outreach/policy");
  const [f, setF] = useState<OutreachPolicy | null>(null);
  useEffect(() => { if (data) setF(data); }, [data]);
  if (!f) return <Skeleton className="h-64" />;
  const num = (v: string) => (v === "" ? 0 : Number(v));
  const set = <K extends keyof OutreachPolicy>(k: K, v: OutreachPolicy[K]) => setF({ ...f, [k]: v });
  const save = async () => {
    try { await api("/outreach/policy", { method: "PUT", json: f }); toast("success", "Messaging policy saved"); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Save failed"); }
  };
  const d = !isAdmin;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Messaging policy</CardTitle>
        <CardDescription>Applies to every sequence. {isAdmin ? "Changes are audited." : "Only admins can change it."}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-5">
        <div className="grid gap-3 rounded-lg border p-3">
          <Field label="Sending mode" htmlFor="p-mode">
            <Select id="p-mode" disabled={d} value={f.sending_mode} onChange={(e) => set("sending_mode", e.target.value as OutreachPolicy["sending_mode"])}>
              <option value="draft_only">Draft-only — approved emails go into the mailbox as drafts; a person presses send</option>
              <option value="send">Send — approved emails send automatically in the sending window</option>
            </Select>
          </Field>
          {!f.external_sending_allowed && (
            <p className="flex gap-2 text-xs text-amber-900"><ShieldAlert className="size-4 shrink-0" />External sending is switched off on the server (OUTREACH_EXTERNAL_SENDING_ALLOWED=false). Real Gmail/Outlook mailboxes stay in draft-only mode whatever is chosen here; only the test mailbox can &ldquo;send&rdquo;.</p>
          )}
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Max emails per prospect" htmlFor="p-max" hint="First email + follow-ups (1–6)">
            <Input id="p-max" type="number" min={1} max={6} disabled={d} value={f.max_attempts}
              onChange={(e) => { const n = Math.max(1, Math.min(6, num(e.target.value))); const sd = [...f.spacing_days]; while (sd.length < n) sd.push(7); setF({ ...f, max_attempts: n, spacing_days: sd }); }} />
          </Field>
          <Field label="Days between emails" htmlFor="p-sp" hint="Comma-separated; first is 0">
            <Input id="p-sp" disabled={d} value={f.spacing_days.slice(0, f.max_attempts).join(", ")}
              onChange={(e) => set("spacing_days", e.target.value.split(",").map((x) => num(x.trim())))} />
          </Field>
          <Field label="Out-of-office: pause follow-ups (days)" htmlFor="p-ooo"><Input id="p-ooo" type="number" disabled={d} value={f.ooo_postpone_days} onChange={(e) => set("ooo_postpone_days", num(e.target.value))} /></Field>
          <Field label="Emails per day, all mailboxes" htmlFor="p-gl"><Input id="p-gl" type="number" disabled={d} value={f.global_daily_limit} onChange={(e) => set("global_daily_limit", num(e.target.value))} /></Field>
          <Field label="Emails per day to one institution" htmlFor="p-dl"><Input id="p-dl" type="number" disabled={d} value={f.per_domain_daily_limit} onChange={(e) => set("per_domain_daily_limit", num(e.target.value))} /></Field>
          <Field label="Word limit: first / follow-up" htmlFor="p-w1">
            <div className="flex gap-2"><Input id="p-w1" type="number" disabled={d} value={f.max_words_first} onChange={(e) => set("max_words_first", num(e.target.value))} /><Input aria-label="Follow-up word limit" type="number" disabled={d} value={f.max_words_follow_up} onChange={(e) => set("max_words_follow_up", num(e.target.value))} /></div>
          </Field>
          <Field label="Sending window (hours)" htmlFor="p-ws">
            <div className="flex items-center gap-2"><Input id="p-ws" type="number" min={0} max={23} disabled={d} value={f.send_window.start_hour} onChange={(e) => set("send_window", { ...f.send_window, start_hour: num(e.target.value) })} /><span>to</span><Input aria-label="Window end hour" type="number" min={1} max={24} disabled={d} value={f.send_window.end_hour} onChange={(e) => set("send_window", { ...f.send_window, end_hour: num(e.target.value) })} /></div>
          </Field>
          <Field label="Time zone" htmlFor="p-tz"><Input id="p-tz" disabled={d} value={f.send_window.timezone} onChange={(e) => set("send_window", { ...f.send_window, timezone: e.target.value })} /></Field>
          <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" disabled={d} className="accent-[#6D28D9]" checked={f.send_window.weekdays_only} onChange={(e) => set("send_window", { ...f.send_window, weekdays_only: e.target.checked })} />Weekdays only</label>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Field label="Signature" htmlFor="p-sig" hint="{sender_name} is replaced with the mailbox name"><Textarea id="p-sig" rows={3} disabled={d} value={f.signature} onChange={(e) => set("signature", e.target.value)} /></Field>
          <Field label="Opt-out line (required)" htmlFor="p-opt"><Textarea id="p-opt" rows={3} disabled={d} value={f.opt_out_line} onChange={(e) => set("opt_out_line", e.target.value)} /></Field>
          <Field label="Banned phrases" htmlFor="p-ban" hint="One per line. Hype words and anything that implies an existing relationship."><Textarea id="p-ban" rows={6} disabled={d} value={f.banned_phrases.join("\n")} onChange={(e) => set("banned_phrases", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))} /></Field>
          <Field label="Restricted names" htmlFor="p-res" hint="One per line. Only allowed when an approved claim or a verified fact mentions them (stops invented integrations)."><Textarea id="p-res" rows={6} disabled={d} value={f.restricted_terms.join("\n")} onChange={(e) => set("restricted_terms", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))} /></Field>
        </div>
        {isAdmin && <div><Button onClick={save}><Save />Save policy</Button></div>}
      </CardContent>
    </Card>
  );
}

function ConnectDialog({ open, onOpenChange, providers, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; providers: MailProvider[]; onDone: () => void }) {
  const [f, setF] = useState({ provider: "mock", email: "", name: "", shared: true, note: "", confirm: false });
  useEffect(() => { if (open) setF({ provider: providers.find((p) => p.available)?.key ?? "mock", email: "", name: "", shared: true, note: "", confirm: false }); }, [open, providers]);
  const s = useSubmit(async () => {
    if (!f.confirm) throw new Error("Confirm that you are authorized to send from this mailbox");
    if (f.provider === "mock") {
      return api("/outreach/mailboxes/mock", { method: "POST", json: { email_address: f.email, display_name: f.name, is_shared: f.shared, authorization_note: f.note, confirm_authorized: true } });
    }
    const r = await api<{ url: string }>(`/outreach/oauth/${f.provider}/start${qs({ authorization_note: f.note, is_shared: f.shared, confirm_authorized: true })}`);
    window.location.href = r.url;
    return null;
  }, () => { onOpenChange(false); onDone(); }, f.provider === "mock" ? "Test mailbox added — an admin must approve it" : "Opening sign-in…");
  const p = providers.find((x) => x.key === f.provider);
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Connect a mailbox" wide onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel={f.provider === "mock" ? "Add test mailbox" : "Continue to sign-in"}
      description="Sign-in uses OAuth with the smallest permissions needed. Passwords are never asked for or stored.">
      <Field label="Provider" htmlFor="mb-p">
        <Select id="mb-p" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })}>
          {providers.map((x) => <option key={x.key} value={x.key} disabled={!x.available}>{x.name}{x.available ? "" : " — not configured"}</option>)}
        </Select>
        {p && <span className="text-xs text-muted-foreground">{p.note}</span>}
      </Field>
      {f.provider === "mock" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Address" htmlFor="mb-e"><Input id="mb-e" type="email" placeholder="sales-test@plugdrop.example" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Sender name" htmlFor="mb-n"><Input id="mb-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        </div>
      )}
      <Field label="Mailbox type" htmlFor="mb-s">
        <Select id="mb-s" value={f.shared ? "shared" : "personal"} onChange={(e) => setF({ ...f, shared: e.target.value === "shared" })}>
          <option value="shared">Shared team mailbox (e.g. sales@)</option>
          <option value="personal">My personal work mailbox — only I can send from it</option>
        </Select>
      </Field>
      <Field label="Who authorized sending from this mailbox?" htmlFor="mb-a" hint="Recorded in the audit log, e.g. “Approved by Head of Sales, 12 Oct 2026”">
        <Input id="mb-a" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
      </Field>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-0.5 accent-[#6D28D9]" checked={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.checked })} />I am authorized to send business email from this mailbox on behalf of PlugDrop.AI.</label>
    </FormDialog>
  );
}

function Mailboxes() {
  const toast = useToast();
  const isAdmin = useCan("admin");
  const { data, mutate } = useApi<Mailbox[]>("/outreach/mailboxes");
  const { data: providers } = useApi<MailProvider[]>("/outreach/providers");
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    if (sp.get("connected")) toast("success", "Mailbox connected. An admin must approve it before use.");
    if (sp.get("mailbox_error")) toast("error", `Mailbox not connected: ${sp.get("mailbox_error")}`);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const patch = async (m: Mailbox, json: Record<string, unknown>, ok: string) => {
    try { await api(`/outreach/mailboxes/${m.id}`, { method: "PATCH", json }); toast("success", ok); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div><CardTitle>Mailboxes</CardTitle><CardDescription className="mt-1">A mailbox must be approved by an admin before use. Sending also needs the admin switch below.</CardDescription></div>
        <Button size="sm" onClick={() => setOpen(true)} disabled={!providers}><Plus />Connect mailbox</Button>
      </CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Mailbox</TH><TH>Status</TH><TH>Mode</TH><TH>Daily limit</TH><TH /></TR></THead>
        <TBody>
          {data?.map((m) => (
            <TR key={m.id}>
              <TD>
                <div className="flex items-center gap-1.5 font-medium"><Mail className="size-3.5 text-muted-foreground" />{m.display_name}</div>
                <div className="text-xs text-muted-foreground">{m.email_address} · {m.provider === "mock" ? "Test (mock)" : m.provider === "gmail" ? "Gmail" : "Microsoft 365"} · {m.is_shared ? "shared" : `personal (${m.owner?.full_name ?? "—"})`}</div>
                <div className="text-[11px] text-muted-foreground">Authorization: {m.authorization_note}</div>
                {m.last_error && <div className="text-xs text-red-700">{m.last_error}</div>}
              </TD>
              <TD><Badge variant={m.status === "active" ? "success" : m.status === "pending_approval" ? "warning" : "danger"}>{label(m.status)}</Badge>{m.last_sync_at && <div className="mt-0.5 text-[11px] text-muted-foreground">Synced {relative(m.last_sync_at)}</div>}</TD>
              <TD><ModeNotice mode={m.effective_mode} reason={m.effective_mode_reason} /><div className="mt-0.5 max-w-56 text-[11px] text-muted-foreground">{m.effective_mode_reason}</div></TD>
              <TD>{isAdmin ? <Input aria-label="Daily limit" type="number" min={1} max={500} defaultValue={m.daily_send_limit} className="h-8 w-20" onBlur={(e) => Number(e.target.value) !== m.daily_send_limit && patch(m, { daily_send_limit: Number(e.target.value) }, "Limit updated")} /> : m.daily_send_limit}</TD>
              <TD className="text-right">
                {isAdmin && (
                  <div className="flex flex-wrap justify-end gap-1.5">
                    {m.status !== "active" && <Button size="sm" onClick={() => patch(m, { status: "active" }, "Mailbox approved")}><Check />Approve</Button>}
                    {m.status === "active" && <Button size="sm" variant="outline" onClick={() => patch(m, { sending_enabled: !m.sending_enabled }, m.sending_enabled ? "Sending switched off" : "Sending switched on")}>{m.sending_enabled ? "Turn sending off" : "Allow sending"}</Button>}
                    {m.status === "active" && <Button size="sm" variant="ghost" onClick={() => patch(m, { status: "disabled" }, "Mailbox disabled")}>Disable</Button>}
                  </div>
                )}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.length === 0 && <EmptyState title="No mailboxes yet" description="Connect a shared team mailbox. Until Gmail/Outlook are configured, use a test mailbox." />}
      {providers && <ConnectDialog open={open} onOpenChange={setOpen} providers={providers} onDone={() => mutate()} />}
    </Card>
  );
}

const KIND_LABEL: Record<ContentItem["kind"], string> = { value_prop: "Value proposition", cta: "Call to action", claim: "Approved claim", case_study: "Case study" };

function ContentDialog({ open, onOpenChange, initial, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; initial?: ContentItem; onDone: () => void }) {
  const blank = { kind: "value_prop", title: "", body: "", use_cases: [] as string[], source_note: "" };
  const [f, setF] = useState(blank);
  useEffect(() => { if (open) setF(initial ? { kind: initial.kind, title: initial.title, body: initial.body, use_cases: initial.use_cases, source_note: initial.source_note ?? "" } : blank); }, [open, initial]); // eslint-disable-line react-hooks/exhaustive-deps
  const s = useSubmit(() => api(initial ? `/outreach/content/${initial.id}` : "/outreach/content", { method: initial ? "PUT" : "POST", json: { ...f, source_note: f.source_note || null } }),
    () => { onOpenChange(false); onDone(); }, initial ? "Saved — needs admin approval again" : "Added — an admin must approve it before emails can use it");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={initial ? "Edit content" : "Add to content library"} wide onSubmit={s.submit} busy={s.busy} error={s.error}
      description="Emails may only use approved items. Don't add customer names, results or integrations you can't back up.">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Type" htmlFor="ct-k"><Select id="ct-k" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
        <Field label="Title (internal)" htmlFor="ct-t"><Input id="ct-t" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      </div>
      <Field label="Text used in emails" htmlFor="ct-b"><Textarea id="ct-b" rows={4} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></Field>
      <fieldset className="grid gap-1.5"><legend className="mb-1 text-sm font-medium">Use cases (none = any)</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">{USE_CASE_OPTIONS.map((u) => (
          <label key={u.value} className="flex items-center gap-1.5 text-sm"><input type="checkbox" className="accent-[#6D28D9]" checked={f.use_cases.includes(u.value)} onChange={(e) => setF({ ...f, use_cases: e.target.checked ? [...f.use_cases, u.value] : f.use_cases.filter((x) => x !== u.value) })} />{u.label}</label>
        ))}</div>
      </fieldset>
      <Field label="Evidence / source" htmlFor="ct-s" hint="Where this claim comes from, for the approver"><Input id="ct-s" value={f.source_note} onChange={(e) => setF({ ...f, source_note: e.target.value })} /></Field>
    </FormDialog>
  );
}

function ContentLibrary() {
  const toast = useToast();
  const isAdmin = useCan("admin");
  const isManager = useCan("manager");
  const { data, mutate } = useApi<ContentItem[]>("/outreach/content");
  const [edit, setEdit] = useState<ContentItem | undefined>();
  const [open, setOpen] = useState(false);
  const status = async (c: ContentItem, st: string) => {
    try { await api(`/outreach/content/${c.id}/status?status=${st}`, { method: "POST" }); toast("success", `Marked ${st}`); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div><CardTitle>Content library</CardTitle><CardDescription className="mt-1">Approved value propositions, claims, case studies and calls to action. Managers add; admins approve.</CardDescription></div>
        {isManager && <Button size="sm" onClick={() => { setEdit(undefined); setOpen(true); }}><Plus />Add</Button>}
      </CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Item</TH><TH>Use cases</TH><TH>Status</TH><TH /></TR></THead>
        <TBody>
          {data?.map((c) => (
            <TR key={c.id}>
              <TD className="max-w-xl"><div className="flex flex-wrap items-center gap-1.5"><Badge variant="muted">{KIND_LABEL[c.kind]}</Badge><span className="font-medium">{c.title}</span><DemoBadge show={c.is_demo} /></div><p className="mt-1 text-sm text-muted-foreground">{c.body}</p>{c.source_note && <p className="mt-0.5 text-[11px] text-muted-foreground">Source: {c.source_note}</p>}</TD>
              <TD className="text-xs text-muted-foreground">{c.use_cases.length ? c.use_cases.map(ucLabel).join(", ") : "Any"}</TD>
              <TD><Badge variant={c.status === "approved" ? "success" : c.status === "draft" ? "warning" : "muted"}>{label(c.status)}</Badge></TD>
              <TD className="text-right"><div className="flex flex-wrap justify-end gap-1.5">
                {isManager && <Button size="sm" variant="ghost" onClick={() => { setEdit(c); setOpen(true); }}>Edit</Button>}
                {isAdmin && c.status !== "approved" && <Button size="sm" variant="outline" onClick={() => status(c, "approved")}><Check />Approve</Button>}
                {isAdmin && c.status === "approved" && <Button size="sm" variant="ghost" onClick={() => status(c, "retired")}>Retire</Button>}
              </div></TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.length === 0 && <EmptyState title="The content library is empty" description="Emails need at least one approved value proposition and call to action." />}
      <ContentDialog open={open} onOpenChange={setOpen} initial={edit} onDone={() => mutate()} />
    </Card>
  );
}

function Templates() {
  const toast = useToast();
  const isAdmin = useCan("admin");
  const isManager = useCan("manager");
  const { data, mutate } = useApi<EmailTemplateT[]>("/outreach/templates");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", step: 1, use_case: "", subject: "", body: "" });
  const s = useSubmit(() => api("/outreach/templates", { method: "POST", json: { ...f, use_case: f.use_case || null } }), () => { setOpen(false); mutate(); }, "Template added — an admin must approve it");
  const status = async (t: EmailTemplateT, st: string) => {
    try { await api(`/outreach/templates/${t.id}/status?status=${st}`, { method: "POST" }); toast("success", `Marked ${st}`); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div><CardTitle>Templates</CardTitle><CardDescription className="mt-1">Optional subject lines per step. Use {"{inst}"} for the institution name.</CardDescription></div>
        {isManager && <Button size="sm" onClick={() => { setF({ name: "", step: 1, use_case: "", subject: "", body: "" }); setOpen(true); }}><Plus />Add</Button>}
      </CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Template</TH><TH>Step</TH><TH>Use case</TH><TH>Status</TH><TH /></TR></THead>
        <TBody>
          {data?.map((t) => (
            <TR key={t.id}>
              <TD><div className="font-medium">{t.name} <DemoBadge show={t.is_demo} /></div><div className="text-xs text-muted-foreground">Subject: {t.subject}</div></TD>
              <TD>{t.step}</TD>
              <TD className="text-xs text-muted-foreground">{t.use_case ? ucLabel(t.use_case) : "Any"}</TD>
              <TD><Badge variant={t.status === "approved" ? "success" : t.status === "draft" ? "warning" : "muted"}>{label(t.status)}</Badge></TD>
              <TD className="text-right">
                {isAdmin && t.status !== "approved" && <Button size="sm" variant="outline" onClick={() => status(t, "approved")}><Check />Approve</Button>}
                {isAdmin && t.status === "approved" && <Button size="sm" variant="ghost" onClick={() => status(t, "retired")}>Retire</Button>}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.length === 0 && <EmptyState title="No templates" description="Without a template, subjects come from the use case." />}
      <FormDialog open={open} onOpenChange={setOpen} title="Add template" onSubmit={s.submit} busy={s.busy} error={s.error}>
        <Field label="Name" htmlFor="tp-n"><Input id="tp-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Step" htmlFor="tp-s"><Input id="tp-s" type="number" min={1} max={6} value={f.step} onChange={(e) => setF({ ...f, step: Number(e.target.value) })} /></Field>
          <Field label="Use case" htmlFor="tp-u"><Select id="tp-u" value={f.use_case} onChange={(e) => setF({ ...f, use_case: e.target.value })}><option value="">Any</option>{USE_CASE_OPTIONS.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}</Select></Field>
        </div>
        <Field label="Subject" htmlFor="tp-sub"><Input id="tp-sub" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} /></Field>
      </FormDialog>
    </Card>
  );
}

export function OutreachSettings() {
  return (
    <div className="grid gap-6">
      <Mailboxes />
      <Policy />
      <ContentLibrary />
      <Templates />
    </div>
  );
}
