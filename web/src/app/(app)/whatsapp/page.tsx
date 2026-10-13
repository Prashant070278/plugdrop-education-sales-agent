"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, Check, CheckCheck, Clock, Hand, Send, Sparkles, UserCheck, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { DemoBadge, EmptyState, ErrorState, Field, PageHeader, SearchInput } from "@/components/common";
import { useCan, useUser } from "@/components/auth";
import { useToast } from "@/components/toast";
import { api, useApi } from "@/lib/api";
import { dateTime, label, relative } from "@/lib/format";
import type { WaConversation, WaConversationDetail, WaMessage, WaStatus, WaTemplate } from "@/lib/types";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  interested: "Interested", meeting_request: "Meeting request", needs_information: "Needs information", not_now: "Not now",
  not_interested: "Not interested", referral: "Referral", out_of_office: "Auto-reply", unclear: "Unclear",
};

function StatusTick({ m }: { m: WaMessage }) {
  if (m.status === "read") return <span className="inline-flex items-center gap-0.5 text-sky-600"><CheckCheck className="size-3.5" />Read</span>;
  if (m.status === "delivered") return <span className="inline-flex items-center gap-0.5"><CheckCheck className="size-3.5" />Delivered</span>;
  if (m.status === "sent") return <span className="inline-flex items-center gap-0.5"><Check className="size-3.5" />Sent</span>;
  if (m.status === "queued" || m.status === "sending") return <span className="inline-flex items-center gap-0.5"><Clock className="size-3.5" />{m.status === "queued" ? "Waiting to send" : "Sending"}</span>;
  if (m.status === "failed" || m.status === "blocked") return <span className="inline-flex items-center gap-0.5 text-red-700"><AlertTriangle className="size-3.5" />{m.status === "failed" ? "Failed" : "Held back"}</span>;
  return <span>{label(m.status)}</span>;
}

function Bubble({ m, conv, act, canSend }: {
  m: WaMessage; conv: WaConversationDetail; canSend: boolean;
  act: (path: string, init: RequestInit & { json?: unknown }, ok: string) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(m.body);
  const out = m.direction === "out";
  const pending = out && ["draft", "blocked"].includes(m.status);
  return (
    <li className={cn("flex", out ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm shadow-sm sm:max-w-[75%]",
        out ? (pending ? "border border-dashed border-emerald-400 bg-emerald-50/50" : "bg-emerald-50") : "border bg-background")}>
        {pending && <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-emerald-800">Draft — needs approval{m.kind === "template" ? " · template" : ""}</div>}
        {editing ? (
          <div className="grid gap-2">
            <Textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
            <div className="flex gap-2"><Button size="sm" onClick={async () => { if (await act(`/whatsapp/messages/${m.id}`, { method: "PATCH", json: { text } }, "Saved")) setEditing(false); }}>Save</Button><Button size="sm" variant="outline" onClick={() => setEditing(false)}>Cancel</Button></div>
          </div>
        ) : <p className="whitespace-pre-wrap">{m.body}</p>}
        <div className="mt-1 flex flex-wrap items-center justify-end gap-x-2 text-[11px] text-muted-foreground">
          {!out && m.label && <Badge variant={m.opt_out ? "danger" : "secondary"}>{m.opt_out ? "Opted out" : LABELS[m.label] ?? label(m.label)}</Badge>}
          <span title={dateTime(m.sent_at ?? m.created_at)}>{relative(m.sent_at ?? m.created_at)}</span>
          {out && !pending && <StatusTick m={m} />}
        </div>
        {m.error && <div className="mt-1 text-xs text-red-800">{m.error}</div>}
        {pending && !editing && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Button size="sm" disabled={!canSend} onClick={() => act(`/whatsapp/messages/${m.id}/send`, { method: "POST" }, "Sent")}><Send />Approve & send</Button>
            {m.kind === "text" && <Button size="sm" variant="outline" onClick={() => { setText(m.body); setEditing(true); }}>Edit</Button>}
            <Button size="sm" variant="ghost" onClick={() => act(`/whatsapp/messages/${m.id}/cancel`, { method: "POST" }, "Cancelled")}><X />Cancel</Button>
          </div>
        )}
        {!out && m.suggestion && conv.window_open && (
          <button className="mt-2 inline-flex items-center gap-1 rounded-md bg-pdpurple-bg px-2 py-1 text-xs text-pdpurple-deep hover:underline"
            onClick={() => act(`/whatsapp/conversations/${conv.id}/messages`, { method: "POST", json: { text: m.suggestion, purpose: "reply" } }, "Suggested reply drafted — edit and approve it")}>
            <Sparkles className="size-3" />Use suggested reply
          </button>
        )}
      </div>
    </li>
  );
}

function Composer({ conv, act }: { conv: WaConversationDetail; act: (path: string, init: RequestInit & { json?: unknown }, ok: string) => Promise<boolean> }) {
  const [text, setText] = useState("");
  const [tid, setTid] = useState("");
  const [custom, setCustom] = useState<string[]>([]);
  const { data: templates } = useApi<WaTemplate[]>("/whatsapp/templates");
  const approved = (templates ?? []).filter((t) => t.status === "approved");
  const t = approved.find((x) => String(x.id) === tid);
  if (conv.state === "closed" && conv.contact_state?.suppressed) return <p className="text-sm text-red-800">This person opted out. No more messages.</p>;
  return (
    <div className="grid gap-2 border-t pt-3">
      {conv.window_open ? (
        <>
          <Textarea rows={2} placeholder="Write a reply (it's saved as a draft for approval)…" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex justify-between gap-2 text-xs text-muted-foreground">
            <span>Free text allowed until {conv.contact_state?.conversation?.window_open_until ? dateTime(conv.contact_state.conversation.window_open_until) : "24 hours after their last message"}.</span>
            <Button size="sm" disabled={!text.trim()} onClick={async () => { if (await act(`/whatsapp/conversations/${conv.id}/messages`, { method: "POST", json: { text, purpose: "reply" } }, "Draft saved — approve it to send")) setText(""); }}>Save draft</Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">More than 24 hours since they last wrote: WhatsApp only allows an approved template now.</p>
          <div className="flex flex-wrap items-end gap-2">
            <Select aria-label="Template" className="max-w-xs" value={tid} onChange={(e) => { setTid(e.target.value); setCustom([]); }}>
              <option value="">Choose a template…</option>
              {approved.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </Select>
            {t?.params.filter((p) => p.source === "custom").map((p, i) => (
              <Input key={i} className="max-w-48" placeholder={p.label} value={custom[i] ?? ""} onChange={(e) => { const c = [...custom]; c[i] = e.target.value; setCustom(c); }} />
            ))}
            <Button size="sm" disabled={!t} onClick={() => act(`/whatsapp/conversations/${conv.id}/messages`, { method: "POST", json: { template_id: t?.id, custom, purpose: "follow_up" } }, "Template drafted — approve it to send")}>Draft</Button>
          </div>
          {t && <p className="rounded-md bg-muted p-2 text-xs">{t.body}</p>}
        </>
      )}
    </div>
  );
}

function ConversationPane({ id, onChange }: { id: number; onChange: () => void }) {
  const toast = useToast();
  const me = useUser();
  const isManager = useCan("manager");
  const { data: conv, error, mutate } = useApi<WaConversationDetail>(`/whatsapp/conversations/${id}`, undefined, { refreshInterval: 15000 });
  const { data: st } = useApi<WaStatus>("/whatsapp/status");
  const [sim, setSim] = useState("");
  if (error) return <ErrorState error={error} retry={() => mutate()} />;
  if (!conv) return <Skeleton className="h-96" />;
  const act = async (path: string, init: RequestInit & { json?: unknown }, ok: string) => {
    try {
      const r = await api<WaConversationDetail>(path, init);
      if (r && "messages" in r) mutate(r, { revalidate: false }); else mutate();
      toast("success", ok); onChange(); return true;
    } catch (e) { toast("error", e instanceof Error ? e.message : "Failed"); mutate(); return false; }
  };
  const lockedForMe = conv.state === "handoff" && conv.assigned_to && conv.assigned_to.id !== me?.id && me?.role !== "admin";
  const name = conv.contact_name ?? conv.profile_name ?? conv.wa_number;
  return (
    <Card className="flex min-h-[60vh] flex-col">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2 space-y-0 border-b pb-3">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">{name} <DemoBadge show={conv.is_demo} /></CardTitle>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {conv.wa_number}{conv.institution_name && <> · {conv.lead_id ? <Link className="text-primary hover:underline" href={`/leads/${conv.lead_id}`}>{conv.institution_name}</Link> : conv.institution_name}</>}
            {!conv.contact_id && <span className="text-amber-800"> · not linked to a CRM contact</span>}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <Badge variant={conv.state === "handoff" ? "warning" : conv.state === "closed" ? "muted" : "secondary"}>{conv.state === "handoff" ? `With ${conv.assigned_to?.full_name ?? "a person"}` : label(conv.state)}</Badge>
            <Badge variant={conv.window_open ? "success" : "muted"}>{conv.window_open ? "24-hour window open" : "Template only"}</Badge>
            {conv.contact_state?.whatsapp_opt_in ? <Badge variant="success">Opt-in recorded</Badge> : <Badge variant="warning">No opt-in on record</Badge>}
          </div>
          {conv.handoff_reason && <div className="mt-1 text-xs text-muted-foreground">Handoff: {conv.handoff_reason}</div>}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {conv.state !== "handoff" && <Button size="sm" variant="outline" onClick={() => act(`/whatsapp/conversations/${conv.id}/assign`, { method: "POST", json: { action: "take" } }, "You've taken over this conversation")}><Hand />Take over</Button>}
          {conv.state === "handoff" && <Button size="sm" variant="outline" onClick={() => act(`/whatsapp/conversations/${conv.id}/assign`, { method: "POST", json: { action: "release" } }, "Returned to the assistant")}><UserCheck />Release</Button>}
          {conv.state !== "closed" && <Button size="sm" variant="ghost" onClick={() => act(`/whatsapp/conversations/${conv.id}/assign`, { method: "POST", json: { action: "close" } }, "Closed")}>Close</Button>}
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3 pt-4">
        {st && !st.sending && <p className="rounded-md bg-amber-50 p-2.5 text-xs text-amber-900">{st.reason}. Drafts can be prepared; they&apos;ll be held until sending is on.</p>}
        {lockedForMe && <p className="rounded-md bg-amber-50 p-2.5 text-xs text-amber-900">{conv.assigned_to?.full_name} has taken over this conversation; only they (or an admin) can reply.</p>}
        <ul className="flex flex-1 flex-col gap-2.5">
          {conv.messages.filter((m) => m.status !== "cancelled").map((m) => <Bubble key={m.id} m={m} conv={conv} act={act} canSend={!lockedForMe} />)}
          {conv.messages.length === 0 && <EmptyState title="No messages yet" />}
        </ul>
        {!lockedForMe && <Composer conv={conv} act={act} />}
        {isManager && st?.provider === "mock" && (
          <div className="grid gap-2 rounded-lg border border-dashed p-3">
            <Field label="Test: simulate a message from them" htmlFor="wa-sim" hint="Test provider only. Try “Can we talk tomorrow at 11am?”, “I want to speak to a person” or “STOP”.">
              <div className="flex gap-2"><Input id="wa-sim" value={sim} onChange={(e) => setSim(e.target.value)} />
                <Button size="sm" variant="outline" disabled={!sim.trim()} onClick={async () => { if (await act("/whatsapp/mock/inbound", { method: "POST", json: { number: conv.wa_number, text: sim, name: conv.profile_name } }, "Message received")) setSim(""); }}>Receive</Button></div>
            </Field>
            {conv.messages.filter((m) => m.direction === "out" && ["sent", "delivered"].includes(m.status)).slice(-1).map((m) => (
              <div key={m.id} className="flex flex-wrap gap-2 text-xs"><span className="text-muted-foreground">Last message:</span>
                {(["delivered", "read", "failed"] as const).map((s) => <button key={s} className="text-primary hover:underline" onClick={() => act("/whatsapp/mock/status", { method: "POST", json: { message_id: m.id, status: s } }, `Marked ${s}`)}>mark {s}</button>)}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function WhatsAppPage() {
  const [filter, setFilter] = useState<"" | "handoff" | "open" | "mine">("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const { data, error, mutate } = useApi<WaConversation[]>("/whatsapp/conversations", { state: filter === "handoff" || filter === "open" ? filter : undefined, mine: filter === "mine" || undefined, q }, { refreshInterval: 20000 });
  const { data: st } = useApi<WaStatus>("/whatsapp/status");
  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("c");
    if (c) setSel(Number(c));
  }, []);
  const pick = (id: number) => { setSel(id); window.history.replaceState(null, "", `?c=${id}`); };
  return (
    <>
      <PageHeader eyebrow="WhatsApp Business Platform" title="WhatsApp"
        description="Conversations with people who opted in. Every message is approved by a person; a STOP reply opts them out of every channel." />
      {st && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <Badge variant={st.sending ? "success" : "warning"}>{st.sending ? "Sending on" : "Sending off"}</Badge>
          <span className="text-muted-foreground">{st.reason}</span>
          {st.provider === "mock" && <Badge variant="muted">Test provider</Badge>}
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-3 [&>*]:min-w-0">
          <div className="flex flex-wrap gap-1.5">
            {([["", "All"], ["handoff", "With a person"], ["open", "Open"], ["mine", "Mine"]] as const).map(([k, t]) => (
              <button key={k} onClick={() => setFilter(k)} aria-pressed={filter === k} className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", filter === k ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>{t}</button>
            ))}
          </div>
          <SearchInput value={q} onChange={setQ} placeholder="Search name or number…" />
          {error && <ErrorState error={error} retry={() => mutate()} />}
          <Card>
            <ul className="divide-y">
              {!data && <li className="p-3"><Skeleton className="h-10" /></li>}
              {data?.map((c) => (
                <li key={c.id}>
                  <button onClick={() => pick(c.id)} aria-current={sel === c.id} className={cn("w-full p-3 text-left text-sm hover:bg-muted", sel === c.id && "bg-pdpurple-bg/60")}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">{c.contact_name ?? c.profile_name ?? c.wa_number}</span>
                      <span className="flex items-center gap-1">{c.unread > 0 && <Badge>{c.unread}</Badge>}{c.state === "handoff" && <Hand className="size-3.5 text-amber-700" aria-label="With a person" />}</span>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">{c.institution_name ?? c.wa_number}</div>
                    <div className="mt-0.5 truncate text-xs">{c.last_message ?? "—"}</div>
                  </button>
                </li>
              ))}
            </ul>
            {data && data.length === 0 && <EmptyState title="No conversations" description="Start one from a lead (after recording the person's opt-in), or wait for someone to message your number." />}
          </Card>
        </div>
        <div className="min-w-0">{sel ? <ConversationPane key={sel} id={sel} onChange={() => mutate()} /> : <Card><EmptyState title="Choose a conversation" /></Card>}</div>
      </div>
    </>
  );
}
