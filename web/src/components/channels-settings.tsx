"use client";
import { useEffect, useState } from "react";
import { Check, Plus, Save, Send } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { DemoBadge, EmptyState, Field } from "@/components/common";
import { FormDialog, OwnerSelect, useSubmit } from "@/components/forms";
import { useCan } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { label } from "@/lib/format";
import type { CalendarConn, EngagementPolicy, PermissionRow, WaStatus, WaTemplate } from "@/lib/types";

function WhatsAppStatusCard() {
  const { data: st } = useApi<WaStatus>("/whatsapp/status");
  if (!st) return <Skeleton className="h-32" />;
  return (
    <Card>
      <CardHeader><CardTitle>WhatsApp Business Platform</CardTitle><CardDescription className="mt-1">Official Cloud API only — no personal WhatsApp accounts, no unofficial gateways.</CardDescription></CardHeader>
      <CardContent className="grid gap-2 text-sm">
        <div className="flex flex-wrap gap-2">
          <Badge variant={st.provider === "mock" ? "muted" : "secondary"}>{st.provider === "mock" ? "Test provider (nothing leaves the system)" : "Meta Cloud API"}</Badge>
          <Badge variant={st.configured ? "success" : "warning"}>{st.configured ? "Configured" : "Not configured"}</Badge>
          <Badge variant={st.sending ? "success" : "warning"}>{st.sending ? "Sending on" : "Sending off"}</Badge>
          {st.provider !== "mock" && <Badge variant={st.external_sending_allowed ? "success" : "warning"}>Server switch {st.external_sending_allowed ? "on" : "off"}</Badge>}
        </div>
        <p className="text-muted-foreground">{st.reason}</p>
        <p className="text-xs text-muted-foreground">Webhook URL for Meta: <code className="break-all font-mono">{st.webhook_url}</code> (signature checked with the app secret).</p>
      </CardContent>
    </Card>
  );
}

function PolicyCard() {
  const toast = useToast();
  const isAdmin = useCan("admin");
  const { data, mutate } = useApi<EngagementPolicy>("/engagement/policy");
  const [f, setF] = useState<EngagementPolicy | null>(null);
  useEffect(() => { if (data) setF(data); }, [data]);
  if (!f) return <Skeleton className="h-64" />;
  const d = !isAdmin;
  const num = (v: string) => (v === "" ? 0 : Number(v));
  const set = <K extends keyof EngagementPolicy>(k: K, v: EngagementPolicy[K]) => setF({ ...f, [k]: v });
  const n = (k: keyof EngagementPolicy, title: string, hint?: string) => (
    <Field label={title} htmlFor={`ep-${k}`} hint={hint}><Input id={`ep-${k}`} type="number" disabled={d} value={f[k] as number} onChange={(e) => set(k, num(e.target.value) as never)} /></Field>
  );
  const save = async () => {
    try { await api("/engagement/policy", { method: "PUT", json: f }); toast("success", "Channel policy saved"); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Save failed"); }
  };
  return (
    <Card>
      <CardHeader><CardTitle>Channel policy</CardTitle><CardDescription className="mt-1">How WhatsApp, meetings and follow-up tasks behave. {isAdmin ? "Changes are audited." : "Only admins can change it."}</CardDescription></CardHeader>
      <CardContent className="grid gap-5">
        <label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" disabled={d} className="accent-[#6D28D9]" checked={f.whatsapp_enabled} onChange={(e) => set("whatsapp_enabled", e.target.checked)} />WhatsApp sending enabled</label>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {n("whatsapp_daily_limit", "WhatsApp messages per day")}
          {n("whatsapp_per_minute", "WhatsApp messages per minute")}
          {n("whatsapp_max_attempts", "Send attempts on temporary errors")}
          {n("min_hours_between_touches", "Hours between messages to one person", "Across email and WhatsApp")}
          <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" disabled={d} className="accent-[#6D28D9]" checked={f.block_while_awaiting_our_reply} onChange={(e) => set("block_while_awaiting_our_reply", e.target.checked)} />Hold outreach while they wait for our reply</label>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {n("meeting_duration_minutes", "Meeting length (minutes)")}
          {n("meeting_slots_to_propose", "Times to suggest")}
          {n("meeting_days_ahead", "Look this many days ahead")}
          {n("meeting_min_notice_hours", "Minimum notice (hours)")}
          <Field label="Meeting hours" htmlFor="ep-mh"><div className="flex items-center gap-2"><Input id="ep-mh" type="number" disabled={d} value={f.meeting_hours.start_hour} onChange={(e) => set("meeting_hours", { ...f.meeting_hours, start_hour: num(e.target.value) })} /><span>to</span><Input aria-label="Meeting hours end" type="number" disabled={d} value={f.meeting_hours.end_hour} onChange={(e) => set("meeting_hours", { ...f.meeting_hours, end_hour: num(e.target.value) })} /></div></Field>
          <Field label="Time zone" htmlFor="ep-tz"><Input id="ep-tz" disabled={d} value={f.meeting_hours.timezone} onChange={(e) => set("meeting_hours", { ...f.meeting_hours, timezone: e.target.value })} /></Field>
          <Field label="Fixed video link (optional)" htmlFor="ep-vl" hint="Empty: Google Meet / Teams adds one"><Input id="ep-vl" disabled={d} value={f.video_link_template} onChange={(e) => set("video_link_template", e.target.value)} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {n("interested_no_meeting_days", "Book-the-meeting task due after (days)")}
          {n("confirm_meeting_hours_before", "Confirm meeting (hours before)")}
          {n("post_meeting_follow_up_days", "Post-meeting follow-up after (days)")}
          {n("proposal_follow_up_days", "Proposal follow-up after (days quiet)")}
          {n("reconnect_default_days", "Reconnect later default (days)")}
          <Field label="Owner when a lead has none" htmlFor="ep-own"><OwnerSelect id="ep-own" value={f.default_task_owner_id} onChange={(v) => set("default_task_owner_id", v)} /></Field>
        </div>
        {isAdmin && <div><Button onClick={save}><Save />Save policy</Button></div>}
      </CardContent>
    </Card>
  );
}

function PermissionsCard() {
  const toast = useToast();
  const isAdmin = useCan("admin");
  const { data, mutate } = useApi<PermissionRow[]>("/engagement/permissions");
  const change = async (action: string, role: string) => {
    try { await api("/engagement/permissions", { method: "PUT", json: { [action]: role } }); toast("success", "Permission updated"); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <Card>
      <CardHeader><CardTitle>Who can do what</CardTitle><CardDescription className="mt-1">The lowest role allowed for each action. {isAdmin ? "" : "Only admins can change these."}</CardDescription></CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Action</TH><TH>Minimum role</TH></TR></THead>
        <TBody>
          {data?.map((p) => (
            <TR key={p.action}>
              <TD>{p.label}</TD>
              <TD>{isAdmin ? (
                <Select aria-label={`Minimum role for ${p.label}`} className="h-8 w-36" value={p.min_role} onChange={(e) => change(p.action, e.target.value)}>
                  {["rep", "manager", "admin"].map((r) => <option key={r} value={r}>{label(r)}</option>)}
                </Select>
              ) : label(p.min_role)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

function TemplatesCard() {
  const toast = useToast();
  const isManager = useCan("manager");
  const { data, mutate } = useApi<WaTemplate[]>("/whatsapp/templates");
  const { data: st } = useApi<WaStatus>("/whatsapp/status");
  const [open, setOpen] = useState(false);
  const blank = { name: "", category: "marketing", purpose: "first_contact", body: "", params: [] as { label: string; source: string }[] };
  const [f, setF] = useState(blank);
  const s = useSubmit(() => api("/whatsapp/templates", { method: "POST", json: f }), () => { setOpen(false); mutate(); }, "Template saved — submit it to WhatsApp for approval");
  const submit = async (t: WaTemplate) => {
    try { const r = await api<WaTemplate>(`/whatsapp/templates/${t.id}/submit`, { method: "POST" }); toast("success", `Submitted: ${label(r.status)}`); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  const count = (f.body.match(/\{\{\d+\}\}/g) ?? []).length;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div><CardTitle>WhatsApp templates</CardTitle><CardDescription className="mt-1">First messages (and anything after 24 hours of silence) must use a template WhatsApp has approved.</CardDescription></div>
        {isManager && <Button size="sm" onClick={() => { setF(blank); setOpen(true); }}><Plus />New template</Button>}
      </CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Template</TH><TH>Status</TH><TH /></TR></THead>
        <TBody>
          {data?.map((t) => (
            <TR key={t.id}>
              <TD className="max-w-xl"><div className="font-medium">{t.name} <span className="text-xs text-muted-foreground">· {t.category} · {t.language}</span> <DemoBadge show={t.is_demo} /></div><p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{t.body}</p>{t.rejection_reason && <p className="text-xs text-red-800">Rejected: {t.rejection_reason}</p>}</TD>
              <TD><Badge variant={t.status === "approved" ? "success" : t.status === "rejected" ? "danger" : "warning"}>{label(t.status)}</Badge></TD>
              <TD className="text-right">{isManager && ["draft", "rejected"].includes(t.status) && <Button size="sm" variant="outline" onClick={() => submit(t)}><Send />Submit to WhatsApp</Button>}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.length === 0 && <EmptyState title="No templates yet" />}
      <FormDialog open={open} onOpenChange={setOpen} title="New WhatsApp template" wide onSubmit={s.submit} busy={s.busy} error={s.error}
        description="Use {{1}}, {{2}} … for values filled in per message. Marketing templates must say how to opt out (e.g. “Reply STOP to opt out”).">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Name" htmlFor="wt-n" hint="lower_case_with_underscores"><Input id="wt-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Category" htmlFor="wt-c"><Select id="wt-c" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}><option value="marketing">Marketing</option><option value="utility">Utility (e.g. meeting reminder)</option></Select></Field>
          <Field label="Used for" htmlFor="wt-p"><Select id="wt-p" value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })}><option value="first_contact">First message</option><option value="follow_up">Follow-up</option><option value="meeting">Meeting</option><option value="reply">Reply after 24 hours</option></Select></Field>
        </div>
        <Field label="Text" htmlFor="wt-b"><Textarea id="wt-b" rows={4} value={f.body} onChange={(e) => {
          const body = e.target.value; const c = (body.match(/\{\{\d+\}\}/g) ?? []).length;
          const params = Array.from({ length: c }, (_, i) => f.params[i] ?? { label: `Value ${i + 1}`, source: "custom" });
          setF({ ...f, body, params });
        }} /></Field>
        {count > 0 && (
          <div className="grid gap-2">{f.params.map((p, i) => (
            <div key={i} className="grid grid-cols-[60px_1fr_1fr] items-center gap-2 text-sm"><code>{`{{${i + 1}}}`}</code>
              <Input aria-label={`Label for value ${i + 1}`} value={p.label} onChange={(e) => { const ps = [...f.params]; ps[i] = { ...p, label: e.target.value }; setF({ ...f, params: ps }); }} />
              <Select aria-label={`Source for value ${i + 1}`} value={p.source} onChange={(e) => { const ps = [...f.params]; ps[i] = { ...p, source: e.target.value }; setF({ ...f, params: ps }); }}>
                {Object.entries(st?.param_sources ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select></div>
          ))}</div>
        )}
      </FormDialog>
    </Card>
  );
}

function CalendarsCard() {
  const toast = useToast();
  const canApprove = useCan("admin");
  const { data, mutate } = useApi<CalendarConn[]>("/meetings/calendars/connections");
  const { data: providers } = useApi<{ key: string; name: string; available: boolean; note: string }[]>("/meetings/calendars/providers");
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    if (sp.get("calendar_connected")) toast("success", "Calendar connected. An admin must approve it before use.");
    if (sp.get("calendar_error")) toast("error", `Calendar not connected: ${sp.get("calendar_error")}`);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const connect = async (key: string) => {
    try {
      if (key === "mock") { await api("/meetings/calendars/mock", { method: "POST" }); toast("success", "Test calendar added — an admin must approve it"); mutate(); return; }
      const r = await api<{ url: string }>(`/meetings/oauth/${key}/start`);
      window.location.href = r.url;
    } catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  const setStatus = async (c: CalendarConn, status: string) => {
    try { await api(`/meetings/calendars/connections/${c.id}`, { method: "PATCH", json: { status } }); toast("success", status === "active" ? "Approved" : "Disabled"); mutate(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div><CardTitle>Calendars</CardTitle><CardDescription className="mt-1">Each person connects their own calendar (free/busy and creating the meetings they approve). Passwords are never stored.</CardDescription></div>
        <div className="flex flex-wrap gap-2">{providers?.map((p) => <Button key={p.key} size="sm" variant="outline" disabled={!p.available} title={p.note} onClick={() => connect(p.key)}><Plus />{p.name}</Button>)}</div>
      </CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Person</TH><TH>Calendar</TH><TH>Status</TH><TH /></TR></THead>
        <TBody>
          {data?.map((c) => (
            <TR key={c.id}>
              <TD>{c.user_name}</TD>
              <TD><div>{c.provider === "mock" ? "Test calendar" : c.provider === "google" ? "Google Calendar" : "Microsoft 365"}</div><div className="text-xs text-muted-foreground">{c.email_address}</div>{c.last_error && <div className="text-xs text-red-700">{c.last_error}</div>}</TD>
              <TD><Badge variant={c.status === "active" ? "success" : c.status === "pending_approval" ? "warning" : "danger"}>{label(c.status)}</Badge></TD>
              <TD className="text-right">{canApprove && (c.status === "active"
                ? <Button size="sm" variant="ghost" onClick={() => setStatus(c, "disabled")}>Disable</Button>
                : <Button size="sm" variant="outline" onClick={() => setStatus(c, "active")}><Check />Approve</Button>)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.length === 0 && <EmptyState title="No calendars connected" description="Without a calendar the assistant can't suggest free times." />}
    </Card>
  );
}

export function ChannelsSettings() {
  return (
    <div className="grid grid-cols-1 gap-6 [&>*]:min-w-0">
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2 [&>*]:min-w-0"><WhatsAppStatusCard /><PermissionsCard /></div>
      <TemplatesCard />
      <CalendarsCard />
      <PolicyCard />
    </div>
  );
}
