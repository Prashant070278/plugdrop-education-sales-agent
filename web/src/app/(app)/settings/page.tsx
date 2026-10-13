"use client";
import { useEffect, useState } from "react";
import { CheckCircle2, CircleDashed, Clock, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DemoBadge, EmptyState, ErrorState, Field, PageHeader, Pagination } from "@/components/common";
import { ConsentDialog } from "@/components/forms";
import { OutreachSettings } from "@/components/outreach-settings";
import { ChannelsSettings } from "@/components/channels-settings";
import { useCan, useUser } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { date, dateTime, label } from "@/lib/format";
import type { AuditEvent, Campaign, ConsentRecord, Page, SystemInfo, User } from "@/lib/types";

function Integrations() {
  const { data, error } = useApi<SystemInfo>("/settings/system");
  if (error) return <ErrorState error={error} />;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {data?.integrations.map((i) => {
        const Icon = i.status === "connected" ? CheckCircle2 : i.status === "planned" ? Clock : CircleDashed;
        return (
          <Card key={i.key}>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle>{i.name}</CardTitle>
                <Badge variant={i.status === "connected" ? "success" : i.status === "planned" ? "secondary" : "muted"}><Icon className="size-3" />{label(i.status)}</Badge>
              </div>
              <CardDescription>{i.description}</CardDescription>
            </CardHeader>
            {i.env_vars.length > 0 && (
              <CardContent className="text-xs text-muted-foreground">
                Configure with environment variables: {i.env_vars.map((v) => <code key={v} className="mr-1 rounded bg-muted px-1 py-0.5 font-mono">{v}</code>)}
              </CardContent>
            )}
          </Card>
        );
      })}
      {data && (
        <Card>
          <CardHeader><CardTitle>System</CardTitle></CardHeader>
          <CardContent className="grid gap-1 text-sm">
            <div>Environment: <span className="font-medium">{data.environment}</span></div>
            <div>Database: <span className="font-mono text-xs">{data.database}</span></div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Users() {
  const toast = useToast();
  const me = useUser();
  const { data, mutate, error } = useApi<User[]>("/users");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ email: "", full_name: "", role: "rep", password: "" });
  const [busy, setBusy] = useState(false);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/users", { method: "POST", json: f });
      toast("success", "User created");
      setOpen(false);
      setF({ email: "", full_name: "", role: "rep", password: "" });
      mutate();
    } catch (err) { toast("error", err instanceof Error ? err.message : "Failed"); } finally { setBusy(false); }
  };
  const update = async (u: User, patch: Partial<User>) => {
    try { await api(`/users/${u.id}`, { method: "PATCH", json: patch }); mutate(); toast("success", "User updated"); }
    catch (err) { toast("error", err instanceof Error ? err.message : "Failed"); }
  };
  if (error) return <ErrorState error={error} />;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0"><CardTitle>Users & roles</CardTitle><Button size="sm" onClick={() => setOpen(true)}><Plus />Add user</Button></CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Name</TH><TH>Email</TH><TH>Role</TH><TH>Status</TH></TR></THead>
        <TBody>
          {data?.map((u) => (
            <TR key={u.id}>
              <TD className="font-medium">{u.full_name}</TD>
              <TD className="text-muted-foreground">{u.email}</TD>
              <TD>
                <Select aria-label={`Role for ${u.full_name}`} className="h-8 w-32" value={u.role} disabled={u.id === me?.id} onChange={(e) => update(u, { role: e.target.value as User["role"] })}>
                  <option value="rep">Rep</option><option value="manager">Manager</option><option value="admin">Admin</option>
                </Select>
              </TD>
              <TD>
                <Button size="sm" variant="outline" disabled={u.id === me?.id} onClick={() => update(u, { is_active: !u.is_active })}>{u.is_active ? "Deactivate" : "Activate"}</Button>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <CardContent className="pt-4 text-xs text-muted-foreground">Reps manage records and their own tasks. Managers can also delete records, run campaigns, record consent and view the full audit log. Admins manage users and demo data.</CardContent>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Add user</DialogTitle></DialogHeader>
          <form onSubmit={create} className="grid gap-4">
            <Field label="Full name" htmlFor="u-name"><Input id="u-name" required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
            <Field label="Email" htmlFor="u-email"><Input id="u-email" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
            <Field label="Role" htmlFor="u-role"><Select id="u-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="rep">Rep</option><option value="manager">Manager</option><option value="admin">Admin</option></Select></Field>
            <Field label="Temporary password" htmlFor="u-pw" hint="At least 10 characters. Share it securely."><Input id="u-pw" type="password" minLength={10} required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
            <DialogFooter><Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button><Button disabled={busy}>Create</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function Suppression() {
  const toast = useToast();
  const isManager = useCan("manager");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const { data, mutate, error } = useApi<Page<ConsentRecord>>("/consent", { page, page_size: 25 });
  const remove = async (r: ConsentRecord) => {
    if (!confirm("Remove this record? The contact may become contactable again.")) return;
    try { await api(`/consent/${r.id}`, { method: "DELETE" }); mutate(); toast("success", "Removed"); }
    catch (err) { toast("error", err instanceof Error ? err.message : "Failed"); }
  };
  if (error) return <ErrorState error={error} />;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div><CardTitle>Opt-outs & suppression</CardTitle><CardDescription className="mt-1">Matching contacts, emails or whole domains are blocked from outreach.</CardDescription></div>
        <Button size="sm" onClick={() => setOpen(true)}><Plus />Add</Button>
      </CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Applies to</TH><TH>Status</TH><TH>Channel</TH><TH>Reason</TH><TH>Recorded</TH><TH /></TR></THead>
        <TBody>
          {data?.items.map((r) => (
            <TR key={r.id}>
              <TD>{r.domain ? <>Domain <code className="font-mono text-xs">{r.domain}</code></> : r.email ?? `Contact #${r.contact_id}`} <DemoBadge show={r.is_demo} /></TD>
              <TD><Badge variant={r.status === "consented" ? "success" : "danger"}>{label(r.status)}</Badge></TD>
              <TD>{label(r.channel)}</TD>
              <TD className="text-muted-foreground">{r.reason ?? "—"}</TD>
              <TD className="text-muted-foreground">{date(r.recorded_at)}</TD>
              <TD className="text-right">{isManager && <Button size="icon" variant="ghost" aria-label="Remove record" onClick={() => remove(r)}><Trash2 /></Button>}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.total === 0 && <EmptyState title="No opt-outs recorded" />}
      {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      <ConsentDialog open={open} onOpenChange={setOpen} onSaved={() => mutate()} />
    </Card>
  );
}

function Campaigns() {
  const toast = useToast();
  const isManager = useCan("manager");
  const { data, mutate } = useApi<Campaign[]>("/campaigns");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", description: "", channel: "email", status: "draft", start_date: "", end_date: "" });
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api("/campaigns", { method: "POST", json: { ...f, start_date: f.start_date || null, end_date: f.end_date || null, description: f.description || null } });
      toast("success", "Campaign created"); setOpen(false); mutate();
    } catch (err) { toast("error", err instanceof Error ? err.message : "Failed"); }
  };
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0"><CardTitle>Campaigns</CardTitle>{isManager && <Button size="sm" onClick={() => setOpen(true)}><Plus />New campaign</Button>}</CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>Name</TH><TH>Channel</TH><TH>Status</TH><TH>Dates</TH><TH className="text-right">Leads</TH></TR></THead>
        <TBody>
          {data?.map((c) => (
            <TR key={c.id}>
              <TD><div className="font-medium">{c.name}</div><div className="text-xs text-muted-foreground">{c.description}</div></TD>
              <TD>{label(c.channel)}</TD>
              <TD><Badge variant={c.status === "active" ? "success" : "muted"}>{label(c.status)}</Badge></TD>
              <TD className="text-muted-foreground">{date(c.start_date)} – {date(c.end_date)}</TD>
              <TD className="text-right tabular">{c.lead_count}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.length === 0 && <EmptyState title="No campaigns yet" />}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>New campaign</DialogTitle></DialogHeader>
          <form onSubmit={create} className="grid gap-4">
            <Field label="Name" htmlFor="cp-name"><Input id="cp-name" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Channel" htmlFor="cp-ch"><Select id="cp-ch" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{["email", "call", "whatsapp", "linkedin", "multi"].map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
              <Field label="Status" htmlFor="cp-st"><Select id="cp-st" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{["draft", "active", "paused", "completed"].map((x) => <option key={x} value={x}>{label(x)}</option>)}</Select></Field>
              <Field label="Start" htmlFor="cp-s"><Input id="cp-s" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
              <Field label="End" htmlFor="cp-e"><Input id="cp-e" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
            </div>
            <Field label="Description" htmlFor="cp-d"><Textarea id="cp-d" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
            <DialogFooter><Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button><Button>Create</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function AuditLog() {
  const [page, setPage] = useState(1);
  const [entity, setEntity] = useState("");
  const { data, error } = useApi<Page<AuditEvent>>("/audit", { page, page_size: 50, entity_type: entity });
  if (error) return <ErrorState error={error} />;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle>Audit history</CardTitle>
        <select aria-label="Entity" value={entity} onChange={(e) => { setEntity(e.target.value); setPage(1); }} className="h-8 rounded-md border bg-background px-2 text-sm">
          <option value="">All records</option>
          {["institution", "contact", "lead", "opportunity", "task", "activity", "consent_record", "campaign", "user", "system"].map((x) => <option key={x} value={x}>{label(x)}</option>)}
        </select>
      </CardHeader>
      <Table>
        <THead><TR className="hover:bg-transparent"><TH>When</TH><TH>Who</TH><TH>Action</TH><TH>Record</TH><TH>Changed fields</TH></TR></THead>
        <TBody>
          {data?.items.map((e) => (
            <TR key={e.id}>
              <TD className="whitespace-nowrap text-muted-foreground tabular">{dateTime(e.created_at)}</TD>
              <TD>{e.actor?.full_name ?? "System"}</TD>
              <TD><Badge variant="secondary">{label(e.action)}</Badge></TD>
              <TD>{label(e.entity_type)}{e.entity_id ? ` #${e.entity_id}` : ""}</TD>
              <TD className="max-w-md truncate text-xs text-muted-foreground">{["update", "stage_change"].includes(e.action) ? Object.keys(e.changes).map(label).join(", ") : ""}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {data && data.total > 0 && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
    </Card>
  );
}

function DemoData() {
  const toast = useToast();
  const isAdmin = useCan("admin");
  const { data, mutate } = useApi<SystemInfo>("/settings/system");
  const total = data ? Object.values(data.demo_counts).reduce((a, b) => a + b, 0) : 0;
  const purge = async () => {
    if (!confirm("Delete ALL demo records? Real records are not affected.")) return;
    try { await api("/settings/demo-data", { method: "DELETE" }); toast("success", "Demo data removed"); mutate(); }
    catch (err) { toast("error", err instanceof Error ? err.message : "Failed"); }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Demo data</CardTitle>
        <CardDescription>Fictional records created by the seed script. They use reserved <code className="font-mono">.example</code> domains and are marked <DemoBadge show /> everywhere.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap gap-2">{data && Object.entries(data.demo_counts).map(([k, v]) => <Badge key={k} variant="muted">{label(k)}: {v}</Badge>)}</div>
        {isAdmin && <div><Button variant="destructive" disabled={total === 0} onClick={purge}><Trash2 />Remove all demo data</Button></div>}
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const isAdmin = useCan("admin");
  const isManager = useCan("manager");
  const [tab, setTab] = useState("integrations");
  useEffect(() => { const t = new URLSearchParams(window.location.search).get("tab"); if (t) setTab(t); }, []);
  return (
    <>
      <PageHeader eyebrow="Workspace" title="Settings" description="Integrations, outreach, users, compliance and audit." />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-2 h-auto flex-wrap">
          <TabsTrigger value="integrations">Integrations</TabsTrigger>
          <TabsTrigger value="outreach">Email outreach</TabsTrigger>
          <TabsTrigger value="channels">WhatsApp, calendars & permissions</TabsTrigger>
          <TabsTrigger value="suppression">Opt-outs</TabsTrigger>
          <TabsTrigger value="campaigns">Campaigns</TabsTrigger>
          {isManager && <TabsTrigger value="audit">Audit log</TabsTrigger>}
          {isAdmin && <TabsTrigger value="users">Users</TabsTrigger>}
          <TabsTrigger value="demo">Demo data</TabsTrigger>
        </TabsList>
        <TabsContent value="integrations"><Integrations /></TabsContent>
        <TabsContent value="outreach"><OutreachSettings /></TabsContent>
        <TabsContent value="channels"><ChannelsSettings /></TabsContent>
        <TabsContent value="suppression"><Suppression /></TabsContent>
        <TabsContent value="campaigns"><Campaigns /></TabsContent>
        {isManager && <TabsContent value="audit"><AuditLog /></TabsContent>}
        {isAdmin && <TabsContent value="users"><Users /></TabsContent>}
        <TabsContent value="demo"><DemoData /></TabsContent>
      </Tabs>
    </>
  );
}
