"use client";
import { Badge } from "@/components/ui/badge";
import { label } from "@/lib/format";
import type { Meeting } from "@/lib/types";

export const MEETING_STATUS: Record<Meeting["status"], [string, "muted" | "warning" | "secondary" | "success" | "danger"]> = {
  proposed: ["Times suggested", "warning"],
  awaiting_approval: ["Needs approval", "warning"],
  scheduled: ["Booked", "secondary"],
  confirmed: ["Confirmed", "success"],
  completed: ["Held", "muted"],
  cancelled: ["Cancelled", "muted"],
  no_show: ["No-show", "danger"],
};

export function MeetingStatusBadge({ status }: { status: Meeting["status"] }) {
  const [t, v] = MEETING_STATUS[status] ?? [label(status), "muted"];
  return <Badge variant={v}>{t}</Badge>;
}

