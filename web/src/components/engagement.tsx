"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ArrowDownLeft, ArrowRightLeft, ArrowUpRight, CalendarCheck, CheckSquare, Copy, Mail, MessageCircle, Microscope, Network, Phone, StickyNote,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Field } from "@/components/common";
import { FormDialog, useSubmit } from "@/components/forms";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, label, relative } from "@/lib/format";
import {
  TASK_REASONS, type Channel, type Contact, type ContactState, type LinkedinItem, type Meeting, type TimelineEntry, type WaConversationDetail,
  type WaStatus, type WaTemplate,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export const CHANNELS: Record<Channel, { label: string; icon: React.ElementType; cls: string }> = {
  email: { label: "Email", icon: Mail, cls: "bg-sky-50 text-sky-800 border-sky-200" },
  whatsapp: { label: "WhatsApp", icon: MessageCircle, cls: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  meeting: { label: "Meeting", icon: CalendarCheck, cls: "bg-pdpurple-bg text-pdpurple-deep border-pdpurple/30" },
  task: { label: "Task", icon: CheckSquare, cls: "bg-amber-50 text-amber-900 border-amber-200" },
  linkedin: { label: "LinkedIn", icon: Network, cls: "bg-blue-50 text-blue-800 border-blue-200" },
  research: { label: "Research", icon: Microscope, cls: "bg-muted text-muted-foreground" },
  call: { label: "Call", icon: Phone, cls: "bg-muted text-muted-foreground" },
  pipeline: { label: "Pipeline", icon: ArrowRightLeft, cls: "bg-muted text-muted-foreground" },
  note: { label: "Note", icon: StickyNote, cls: "bg-muted text-muted-foreground" },
};

export function ReasonBadge({ reason }: { reason: string | null }) {
  if (!reason) return null;
  return <Badge variant={reason === "manual" ? "muted" : "secondary"}>{TASK_REASONS[reason] ?? label(reason)}</Badge>;
}

/** One timeline across email, WhatsApp, meetings, tasks, LinkedIn and research. */
export function InteractionTimeline({ leadId, contactId, refreshKey }: { leadId?: number; contactId?: number; refreshKey?: number }) {
  const [channel, setChannel] = useState<Channel | "">("");
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const { data } = useApi<TimelineEntry[]>("/engagement/timeline", { lead_id: leadId, contact_id: contactId, channel, r: refreshKey });
  const chips: (Channel | "")[] = ["", "email", "whatsapp", "meeting", "task", "linkedin", "research", "pipeline"];
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Filter by channel">
        {chips.map((c) => (
          <button key={c || "all"} onClick={() => setChannel(c)} aria-pressed={channel === c}
            className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", channel === c ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
            {c ? CHANNELS[c].label : "All channels"}
          </button>
        ))}
      </div>
      {!data ? <div className="grid gap-3"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : data.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing here yet.</p>
      ) : (
        <ol className="relative grid gap-4 before:absolute before:bottom-2 before:left-[15px] before:top-2 before:w-px before:bg-border">
          {data.map((e) => {
            const meta = CHANNELS[e.channel] ?? CHANNELS.note;
            const Icon = meta.icon;
            const Dir = e.direction === "in" ? ArrowDownLeft : e.direction === "out" ? ArrowUpRight : null;
            const long = (e.body?.length ?? 0) > 220;
            return (
              <li key={e.id} className="relative flex gap-3">
                <span className={cn("z-10 flex size-8 shrink-0 items-center justify-center rounded-full border", meta.cls)} title={meta.label}><Icon className="size-3.5" /></span>
                <div className="min-w-0 flex-1 pt-0.5">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                    <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
                      {Dir && <Dir className={cn("size-3.5 shrink-0", e.direction === "in" ? "text-emerald-700" : "text-sky-700")} aria-label={e.direction === "in" ? "From them" : "From us"} />}
                      <span className="min-w-0">{e.subject}</span>
                    </span>
                    <time className="text-xs text-muted-foreground tabular" dateTime={e.at} title={dateTime(e.at)}>{relative(e.at)}</time>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
                    <span>{meta.label}</span><span>· {e.actor ?? "System"}</span>{e.contact && <span>· {e.contact.name}</span>}{e.is_demo && <span>· demo</span>}
                    {e.links.outreach_sequence_id && <Link className="text-primary hover:underline" href={`/outreach/sequences/${e.links.outreach_sequence_id}`}>· open emails</Link>}
                    {e.links.conversation_id && <Link className="text-primary hover:underline" href={`/whatsapp?c=${e.links.conversation_id}`}>· open chat</Link>}
                    {e.links.meeting_id && <Link className="text-primary hover:underline" href={`/meetings/${e.links.meeting_id}`}>· open meeting</Link>}
                  </div>
                  {e.body && (
                    <div className="mt-1.5 rounded-md bg-muted/60 p-2.5 text-sm">
                      <p className={cn("whitespace-pre-wrap", long && !open[e.id] && "line-clamp-3")}>{e.body}</p>
                      {long && <button className="mt-1 text-xs text-primary hover:underline" onClick={() => setOpen({ ...open, [e.id]: !open[e.id] })}>{open[e.id] ? "Show less" : "Show more"}</button>}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/* ---------------- dialogs ---------------- */

export function OptInDialog({ open, onOpenChange, contact, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; contact: Contact; onDone: () => void }) {
  const { data: st } = useApi<WaStatus>(open ? "/whatsapp/status" : null);
  const [f, setF] = useState({ number: "", method: "", evidence: "" });
  useEffect(() => { if (open) setF({ number: contact.whatsapp_number ?? "", method: "", evidence: "" }); }, [open, contact]);
  const s = useSubmit(() => api(`/whatsapp/contacts/${contact.id}/opt-in`, { method: "POST", json: f }), () => { onOpenChange(false); onDone(); }, "WhatsApp opt-in recorded");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={`WhatsApp opt-in: ${contact.full_name}`} onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel="Record opt-in"
      description="WhatsApp only allows business messages to people who agreed to receive them. A phone number found in research or on a website is not consent.">
      <Field label="WhatsApp number (with country code)" htmlFor="oi-n"><Input id="oi-n" placeholder="+91 98123 45678" value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} /></Field>
      <Field label="How did they agree?" htmlFor="oi-m">
        <Select id="oi-m" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>
          <option value="">Choose…</option>
          {Object.entries(st?.opt_in_methods ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </Field>
      <Field label="Evidence (where and when)" htmlFor="oi-e" hint="e.g. “Replied to our email on 12 Oct asking for WhatsApp updates”"><Textarea id="oi-e" rows={2} value={f.evidence} onChange={(e) => setF({ ...f, evidence: e.target.value })} /></Field>
    </FormDialog>
  );
}

export function StartWhatsAppDialog({ open, onOpenChange, contact }: { open: boolean; onOpenChange: (o: boolean) => void; contact: Contact }) {
  const router = useRouter();
  const { data: templates } = useApi<WaTemplate[]>(open ? "/whatsapp/templates" : null);
  const usable = (templates ?? []).filter((t) => t.status === "approved");
  const [tid, setTid] = useState("");
  const [custom, setCustom] = useState<string[]>([]);
  const t = usable.find((x) => String(x.id) === tid);
  useEffect(() => { if (open) { setTid(""); setCustom([]); } }, [open]);
  const customParams = t?.params.filter((p) => p.source === "custom") ?? [];
  const s = useSubmit(async () => {
    if (!t) throw new Error("Choose an approved template");
    return api<WaConversationDetail>("/whatsapp/conversations", { method: "POST", json: { contact_id: contact.id, template_id: t.id, custom } });
  }, (r) => { onOpenChange(false); router.push(`/whatsapp?c=${r.id}`); }, "Message drafted — approve it in WhatsApp");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title={`WhatsApp ${contact.full_name}`} onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel="Draft message"
      description="A first message must be a template WhatsApp has approved. It's drafted for you to approve; nothing is sent yet.">
      <Field label="Template" htmlFor="sw-t">
        <Select id="sw-t" value={tid} onChange={(e) => { setTid(e.target.value); setCustom([]); }}>
          <option value="">{usable.length ? "Choose…" : "No approved templates — add one in Settings → Channels"}</option>
          {usable.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.category})</option>)}
        </Select>
      </Field>
      {t && <p className="whitespace-pre-wrap rounded-md bg-muted p-3 text-sm">{t.body}</p>}
      {customParams.map((p, i) => (
        <Field key={i} label={p.label} htmlFor={`sw-c${i}`}><Input id={`sw-c${i}`} value={custom[i] ?? ""} onChange={(e) => { const c = [...custom]; c[i] = e.target.value; setCustom(c); }} /></Field>
      ))}
    </FormDialog>
  );
}

export function ProposeMeetingDialog({ open, onOpenChange, leadId, contacts }: { open: boolean; onOpenChange: (o: boolean) => void; leadId: number; contacts: Contact[] }) {
  const router = useRouter();
  const [cid, setCid] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => { if (open) { setCid(contacts[0] ? String(contacts[0].id) : ""); setNote(""); } }, [open, contacts]);
  const s = useSubmit(() => api<Meeting>("/meetings", { method: "POST", json: { lead_id: leadId, contact_id: Number(cid), note: note || null } }),
    (m) => { onOpenChange(false); router.push(`/meetings/${m.id}`); }, "Meeting proposed — check the suggested times");
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Propose a meeting" onSubmit={s.submit} busy={s.busy} error={s.error} submitLabel="Suggest times"
      description="The assistant suggests an agenda and free times from your calendar. Nothing is booked until you approve it.">
      <Field label="With" htmlFor="pm-c"><Select id="pm-c" value={cid} onChange={(e) => setCid(e.target.value)}>{contacts.map((c) => <option key={c.id} value={c.id}>{c.full_name}{c.designation ? ` · ${c.designation}` : ""}</option>)}</Select></Field>
      <Field label="What did they say about timing? (optional)" htmlFor="pm-n" hint="e.g. “Thursday afternoon works” — the assistant tries that first"><Textarea id="pm-n" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
    </FormDialog>
  );
}

/* ---------------- LinkedIn ---------------- */

export function LinkedInItemCard({ item, onChange }: { item: LinkedinItem; onChange: () => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(item.body);
  const act = async (path: string, init: RequestInit & { json?: unknown }, ok: string) => {
    try { await api(path, init); toast("success", ok); onChange(); } catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  const failed = (item.quality.checks ?? []).filter((c) => !c.passed);
  const sendable = item.kind === "connection_draft" || item.kind === "message_draft";
  return (
    <li className="rounded-lg border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{item.title}</span>
        <Badge variant={item.status === "approved" ? "success" : item.status === "done" ? "secondary" : "muted"}>{item.status === "done" ? "Done by hand" : label(item.status)}</Badge>
      </div>
      {editing ? (
        <div className="mt-2 grid gap-2">
          <Textarea rows={6} value={body} onChange={(e) => setBody(e.target.value)} />
          <div className="flex gap-2"><Button size="sm" onClick={async () => { await act(`/linkedin/items/${item.id}`, { method: "PATCH", json: { body } }, "Saved"); setEditing(false); }}>Save</Button><Button size="sm" variant="outline" onClick={() => setEditing(false)}>Cancel</Button></div>
        </div>
      ) : <p className="mt-2 whitespace-pre-wrap rounded-md bg-muted/60 p-2.5">{item.body}</p>}
      {sendable && <div className="mt-1 text-xs text-muted-foreground">{item.body.length} / {item.kind === "connection_draft" ? 300 : 700} characters</div>}
      {failed.length > 0 && <ul className="mt-1 text-xs text-red-800">{failed.map((c) => <li key={c.id}>{c.label}{c.detail ? ` — ${c.detail}` : ""}</li>)}</ul>}
      {item.status !== "done" && !editing && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => { setBody(item.body); setEditing(true); }}>Edit</Button>
          <Button size="sm" variant="outline" onClick={() => { navigator.clipboard?.writeText(item.body); toast("success", "Copied — paste it into LinkedIn yourself"); }}><Copy />Copy</Button>
          {sendable && item.status === "draft" && <Button size="sm" disabled={failed.length > 0} onClick={() => act(`/linkedin/items/${item.id}/approve`, { method: "POST" }, "Approved")}>Approve</Button>}
          {(item.status === "approved" || !sendable) && <Button size="sm" variant="outline" onClick={() => act(`/linkedin/items/${item.id}/done`, { method: "POST", json: {} }, "Recorded on the timeline")}>{sendable ? "I've sent it on LinkedIn" : "Mark done"}</Button>}
          <Button size="sm" variant="ghost" onClick={() => act(`/linkedin/items/${item.id}/discard`, { method: "POST" }, "Discarded")}>Discard</Button>
        </div>
      )}
    </li>
  );
}

export function LinkedInDialog({ open, onOpenChange, contact }: { open: boolean; onOpenChange: (o: boolean) => void; contact: Contact }) {
  const toast = useToast();
  const { data, mutate } = useApi<LinkedinItem[]>(open ? "/linkedin/items" : null, { contact_id: contact.id });
  const gen = async (kind: string) => {
    try { await api("/linkedin/generate", { method: "POST", json: { kind, contact_id: contact.id } }); mutate(); } catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>LinkedIn: {contact.full_name}</DialogTitle>
          <DialogDescription>Drafts only. Nothing is sent or collected from LinkedIn automatically: copy the text, send it yourself, then record it here.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => gen("note")}>Research note</Button>
          <Button size="sm" variant="outline" onClick={() => gen("connection_draft")}>Connection note</Button>
          <Button size="sm" variant="outline" onClick={() => gen("message_draft")}>Message after connecting</Button>
          {contact.linkedin_url && <a href={contact.linkedin_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center px-2 text-sm text-primary hover:underline">Open profile</a>}
        </div>
        <ul className="grid max-h-[55vh] gap-2 overflow-auto">
          {data?.map((i) => <LinkedInItemCard key={i.id} item={i} onChange={() => mutate()} />)}
          {data && data.length === 0 && <p className="text-sm text-muted-foreground">No drafts yet.</p>}
        </ul>
      </DialogContent>
    </Dialog>
  );
}

/* ---------------- per-contact engagement card ---------------- */

function ContactRow({ contact, leadId, onChange }: { contact: Contact; leadId: number; onChange: () => void }) {
  const { data: st, mutate } = useApi<ContactState>(`/engagement/contacts/${contact.id}/state`);
  const [dlg, setDlg] = useState<null | "optin" | "wa" | "li">(null);
  const done = () => { mutate(); onChange(); };
  return (
    <li className="rounded-lg border p-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{contact.full_name}</span>
        <span className="text-xs text-muted-foreground">{contact.designation}</span>
      </div>
      {!st ? <Skeleton className="mt-2 h-5" /> : (
        <>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {st.suppressed && <Badge variant="danger">Opted out — no contact</Badge>}
            {st.awaiting_our_reply && <Badge variant="warning">Waiting for our reply</Badge>}
            {st.live_sequence && <Badge variant="secondary">Email sequence {st.live_sequence.status}</Badge>}
            {st.meeting && <Badge variant="success">Meeting {label(st.meeting.status)}</Badge>}
            {st.whatsapp_opt_in ? <Badge variant="success">WhatsApp opt-in</Badge> : <Badge variant="muted">No WhatsApp opt-in</Badge>}
            {st.conversation?.state === "handoff" && <Badge variant="warning">WhatsApp: with a person</Badge>}
            {st.conversation?.window_open_until && <Badge variant="secondary">Chat window open</Badge>}
          </div>
          {st.last_outbound_at && <div className="mt-1 text-xs text-muted-foreground">Last message from us {relative(st.last_outbound_at)}{st.last_inbound_at ? ` · from them ${relative(st.last_inbound_at)}` : ""}</div>}
          {st.checks && !st.checks.email_follow_up.allowed && !st.suppressed && (
            <div className="mt-1 text-xs text-amber-900">Automated outreach on hold: {st.checks.email_follow_up.checks.filter((c) => c.kind === "block" || c.kind === "defer").map((c) => c.detail).join("; ")}</div>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {!st.suppressed && !st.whatsapp_opt_in && <Button size="sm" variant="outline" onClick={() => setDlg("optin")}>Record WhatsApp opt-in</Button>}
            {!st.suppressed && st.whatsapp_opt_in && !st.conversation && <Button size="sm" variant="outline" onClick={() => setDlg("wa")}><MessageCircle />WhatsApp</Button>}
            {st.conversation && <Button size="sm" variant="outline" asChild><Link href={`/whatsapp?c=${st.conversation.id}`}><MessageCircle />Open chat</Link></Button>}
            {st.meeting && <Button size="sm" variant="outline" asChild><Link href={`/meetings/${st.meeting.id}`}><CalendarCheck />Meeting</Link></Button>}
            {!st.suppressed && <Button size="sm" variant="outline" onClick={() => setDlg("li")}><Network />LinkedIn</Button>}
          </div>
        </>
      )}
      <OptInDialog open={dlg === "optin"} onOpenChange={(o) => setDlg(o ? "optin" : null)} contact={contact} onDone={done} />
      <StartWhatsAppDialog open={dlg === "wa"} onOpenChange={(o) => setDlg(o ? "wa" : null)} contact={contact} />
      <LinkedInDialog open={dlg === "li"} onOpenChange={(o) => { setDlg(o ? "li" : null); if (!o) done(); }} contact={contact} />
      <span className="sr-only">{leadId}</span>
    </li>
  );
}

export function EngagementPanel({ leadId, contacts, onChange }: { leadId: number; contacts: Contact[]; onChange: () => void }) {
  if (contacts.length === 0) return <p className="text-sm text-muted-foreground">Add a contact to engage.</p>;
  return <ul className="grid gap-2">{contacts.map((c) => <ContactRow key={c.id} contact={c} leadId={leadId} onChange={onChange} />)}</ul>;
}
