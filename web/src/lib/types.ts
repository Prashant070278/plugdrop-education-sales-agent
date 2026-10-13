export type Role = "admin" | "manager" | "rep";
export type Freshness = "fresh" | "aging" | "stale" | "never";

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface UserRef { id: number; full_name: string }
export interface User extends UserRef { email: string; role: Role; is_active: boolean }

export const INSTITUTION_TYPES = [
  "university", "college", "university_group", "edtech", "online_education", "test_prep", "vocational",
  "professional_education", "other",
] as const;
export type InstitutionType = (typeof INSTITUTION_TYPES)[number];

export const STUDENT_SIZES = ["under_1k", "1k_5k", "5k_20k", "20k_50k", "over_50k", "unknown"] as const;
export type StudentSize = (typeof STUDENT_SIZES)[number];

export const LEAD_STAGES = [
  "new", "researched", "qualified", "outreach_ready", "contacted", "engaged", "meeting_booked", "proposal",
  "negotiation", "won", "lost", "nurture",
] as const;
export type LeadStage = (typeof LEAD_STAGES)[number];

export const OPP_STAGES = ["discovery", "demo", "proposal", "negotiation", "won", "lost"] as const;
export type OppStage = (typeof OPP_STAGES)[number];

export const SOLUTIONS = ["voice_ai", "crm", "chat", "whatsapp", "automation"] as const;
export type Solution = (typeof SOLUTIONS)[number];

export const ACTIVITY_TYPES = ["research", "call", "email", "reply", "meeting", "task", "status_change", "note"] as const;
export type ActivityType = (typeof ACTIVITY_TYPES)[number];

export const VERIFICATION = ["unverified", "verified", "bounced", "invalid"] as const;
export type Verification = (typeof VERIFICATION)[number];

export const PRIORITIES = ["low", "medium", "high", "urgent"] as const;
export type Priority = (typeof PRIORITIES)[number];

export interface Campus { id: number; institution_id: number; name: string; city: string | null; country: string | null; address: string | null }

export interface Institution {
  id: number; name: string; website: string | null; domain: string | null; institution_type: InstitutionType;
  country: string; city: string; student_size_range: StudentSize; courses: string[]; source: string | null;
  notes: string | null; owner: UserRef | null; last_verified_at: string | null; is_demo: boolean;
  created_at: string; updated_at: string; campus_count: number; contact_count: number;
  lead_stage: LeadStage | null; suppressed: boolean; freshness: Freshness;
}
export interface InstitutionDetail extends Institution { campuses: Campus[] }

export interface Contact {
  id: number; institution_id: number; institution: { id: number; name: string }; first_name: string; last_name: string;
  full_name: string; designation: string | null; department: string | null; email: string | null; phone: string | null;
  linkedin_url: string | null; verification_status: Verification; source: string | null; last_verified_at: string | null;
  is_demo: boolean; created_at: string; updated_at: string; suppressed: boolean; freshness: Freshness;
}

export interface ContactRef { id: number; full_name: string; designation?: string | null; email?: string | null }

export interface Lead {
  id: number; institution: { id: number; name: string }; primary_contact: ContactRef | null; owner: UserRef | null;
  campaign_id: number | null; stage: LeadStage; score: number; source: string | null; summary: string | null;
  lost_reason: string | null; stage_changed_at: string; is_demo: boolean; created_at: string; updated_at: string;
  open_value: string; next_task_due: string | null;
}

export interface Opportunity {
  id: number; lead_id: number; institution: { id: number; name: string }; name: string; estimated_value: string;
  currency: string; solution_interest: Solution[]; expected_decision_date: string | null; owner: UserRef | null;
  stage: OppStage; next_action: string | null; next_action_date: string | null; is_demo: boolean;
  created_at: string; updated_at: string;
}

export interface Activity {
  id: number; activity_type: ActivityType; subject: string; body: string | null; occurred_at: string;
  lead_id: number | null; institution_id: number | null; contact: ContactRef | null; opportunity_id: number | null;
  user: UserRef | null; details: Record<string, unknown>; is_demo: boolean;
}

export interface Research {
  id: number; institution_id: number; contact_id: number | null; lead_id: number | null; category: string;
  summary: string; source_url: string | null; confidence: number; found_at: string; is_demo: boolean;
}

export interface Task {
  id: number; title: string; description: string | null; due_at: string | null; reminder_at: string | null;
  priority: Priority; status: "open" | "done"; completed_at: string | null; owner: UserRef | null;
  lead_id: number | null; institution_id: number | null; contact_id: number | null; opportunity_id: number | null;
  is_demo: boolean; created_at: string; institution_name: string | null; overdue: boolean;
}

export interface Campaign {
  id: number; name: string; description: string | null; channel: string; status: string; start_date: string | null;
  end_date: string | null; owner_id: number | null; is_demo: boolean; lead_count: number;
}

export interface ConsentRecord {
  id: number; contact_id: number | null; email: string | null; phone: string | null; domain: string | null;
  channel: string; status: "opted_out" | "suppressed" | "consented"; reason: string | null; source: string | null;
  recorded_at: string; is_demo: boolean;
}

export interface AuditEvent {
  id: number; actor: UserRef | null; action: string; entity_type: string; entity_id: number | null;
  changes: Record<string, unknown>; created_at: string;
}

export interface DuplicateMatch { id: number; label: string; reason: string; exact: boolean }

export interface ImportResult {
  dry_run: boolean; created: number; duplicates: number; errors: number;
  rows: { row: number; status: "created" | "duplicate" | "error"; message: string; id: number | null }[];
}

export interface Dashboard {
  period_days: number; total_prospects: number; total_contacts: number; open_leads: number; qualified_leads: number;
  outreach_activity: number; meetings: number; meetings_booked_leads: number; pipeline_value: string;
  won_value: string; win_rate: number | null; lead_to_meeting_rate: number | null;
  stage_counts: Record<LeadStage, number>; funnel: { stage: LeadStage; reached: number; rate: number }[];
  weekly_activity: { week_start: string; calls: number; emails: number; meetings: number }[];
  tasks_overdue: number; tasks_due_today: number; top_opportunities: Opportunity[]; recent_activity: Activity[];
}

export interface SystemInfo {
  environment: string; database: string;
  integrations: { key: string; name: string; description: string; status: "connected" | "not_configured" | "planned"; env_vars: string[] }[];
  demo_counts: Record<string, number>;
}
