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
  research_score: number | null; research_tier: string | null; last_researched_at: string | null;
}
export interface InstitutionDetail extends Institution { campuses: Campus[] }

export interface Contact {
  id: number; institution_id: number; institution: { id: number; name: string }; salutation: string | null; first_name: string; last_name: string;
  full_name: string; notes: string | null; designation: string | null; department: string | null; email: string | null; phone: string | null;
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
  evidence_level: EvidenceLevel | null; excerpt: string | null; published_at: string | null;
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

/* ---------- Stage 2: research agent ---------- */
export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type EvidenceLevel = "verified_fact" | "source_inference" | "hypothesis";
export type ReviewStatus = "pending" | "approved" | "rejected";

export interface ResearchRunSummary {
  id: number; job_id: number;
  institution: { id: number; name: string; is_demo: boolean; research_score: number | null; research_tier: string | null; last_researched_at: string | null };
  status: RunStatus; attempts: number; max_attempts: number; next_attempt_at: string; last_error: string | null;
  started_at: string | null; finished_at: string | null; provider_calls: number; input_tokens: number; output_tokens: number;
  cost_usd: string; score: number | null; tier: string | null; confidence: "low" | "medium" | "high" | null; pushed_at: string | null;
}

export interface ScoreComponent { label: string; weight: number; ratio: number; points: number; reasons: string[] }

export interface ResearchEvidence {
  id: number; kind: "profile" | "signal" | "use_case"; category: string; category_label: string; claim: string;
  evidence_level: EvidenceLevel; confidence: number; excerpt: string | null; source_url: string | null; source_title: string | null;
  published_at: string | null; retrieved_at: string | null; provider: string; review_status: ReviewStatus; review_note: string | null;
  reviewed_at: string | null; pushed_at: string | null;
}

export interface ResearchCandidate { salutation?: string | null;
  id: number; first_name: string; last_name: string; full_name: string; designation: string | null; department: string | null;
  buying_role: string | null; buying_role_label: string | null; email: string | null; email_status: "none" | "published" | "provider_verified";
  phone: string | null; linkedin_url: string | null; verification_status: string; source_url: string; excerpt: string | null;
  retrieved_at: string | null; provider: string; notes: string | null; existing_contact_id: number | null; review_status: ReviewStatus;
  pushed_contact_id: number | null; pushed_at: string | null;
}

export interface ResearchRunDetail extends ResearchRunSummary {
  score_breakdown: Record<"fit" | "problem" | "intent" | "access" | "quality", ScoreComponent>;
  weights_used: Record<string, number>;
  summary: {
    fit_summary?: string; primary_use_case?: string | null; primary_use_case_label?: string | null; use_case_rationale?: string;
    next_action?: string; missing_information?: { item: string; how_to_verify: string }[];
    decision_makers?: { name: string; designation: string; buying_role_label: string; email_status: string }[];
    profile_confirmed?: boolean; campus_count?: number | null; reasoner_model?: string | null; documents_reviewed?: number;
    best_entry_point?: string | null; opportunity_signal?: string | null; digital_maturity?: string | null; admissions_model?: string | null;
    on_hold?: boolean; sources?: string[];
    workbook?: { file: string; account_id: string; hash: string; research_date: string | null };
  };
  log: { at: string; event: string; [k: string]: unknown }[];
  evidence: ResearchEvidence[]; candidates: ResearchCandidate[]; is_latest: boolean; is_stale: boolean;
}

export interface ResearchJob {
  id: number; status: "queued" | "running" | "completed" | "partial" | "failed" | "cancelled"; label: string; provider: string;
  reasoner: string; requested_by: UserRef | null; total_items: number; cost_usd: string; created_at: string;
  started_at: string | null; finished_at: string | null; counts: Partial<Record<RunStatus, number>>;
}
export interface ResearchJobDetail extends ResearchJob { runs: ResearchRunSummary[] }

export interface ResearchOverview {
  provider: string; reasoner: string; model: string | null; worker: string; tiers: Record<"A" | "B" | "C" | "D", number>;
  queued: number; running: number; stale: number; researched: number; pending_review: number;
  spent_today_usd: number; daily_budget_usd: number; rerun_after_days: number;
}

export interface ResearchConfig {
  weights: { fit: number; problem: number; intent: number; access: number; quality: number };
  tiers: { A: number; B: number; C: number };
  evidence_fresh_days: number; evidence_stale_days: number; rerun_after_days: number;
}

export interface InstitutionResearch { latest: ResearchRunSummary | null; result: ResearchRunDetail | null; history: ResearchRunSummary[] }

export interface WorkbookImportReport {
  file: string; research_date: string | null; dry_run: boolean; accounts_in_file: number; institutions_created: number;
  institutions_updated: number; institutions_unchanged: number; leads_created: number; results_created: number;
  results_unchanged: number; evidence: number; people: number; emails_kept: number; emails_dropped: number;
  phones_dropped: number; people_not_imported: string[]; warnings: string[]; job_id: number | null;
}

/* ---------- outreach (Stage 3) ---------- */
export type MessageStatus = "draft" | "approved" | "scheduled" | "sending" | "drafted_in_mailbox" | "sent" | "delivered" | "bounced" | "failed" | "cancelled" | "blocked";
export type ReplyLabel = "interested" | "meeting_request" | "needs_information" | "not_now" | "not_interested" | "referral" | "out_of_office" | "unclear";
export const REPLY_LABELS: ReplyLabel[] = ["interested", "meeting_request", "needs_information", "not_now", "not_interested", "referral", "out_of_office", "unclear"];
export const USE_CASE_OPTIONS: { value: string; label: string }[] = [
  { value: "voice_enquiry_automation", label: "Voice AI enquiry handling" },
  { value: "enquiry_qualification", label: "Enquiry qualification" },
  { value: "counselling", label: "AI counselling assistant" },
  { value: "missed_call_recovery", label: "Missed-call recovery" },
  { value: "follow_up", label: "Automated follow-up" },
  { value: "whatsapp_engagement", label: "WhatsApp engagement" },
  { value: "crm_integration", label: "CRM integration" },
];

export interface QualityCheck { id: string; label: string; passed: boolean; blocking: boolean; detail: string }
export interface Quality { passed?: boolean; checks?: QualityCheck[] }

export interface OutreachMessage {
  id: number; sequence_id: number; step: number; kind: "sequence" | "reply"; status: MessageStatus; to_email: string;
  subject: string; body: string; generated_by: string; generation: Record<string, unknown>; quality: Quality; edited: boolean;
  planned_offset_days: number; approved_at: string | null; scheduled_for: string | null; sent_at: string | null;
  delivered_at: string | null; provider: string | null; provider_message_id: string | null; last_error: string | null; updated_at: string;
}
export interface EmailReply {
  id: number; sequence_id: number | null; message_id: number | null; from_email: string; subject: string; body: string;
  received_at: string; label: ReplyLabel; label_confidence: number; label_source: string; opt_out: boolean;
  summary: string | null; handled: boolean; handled_at: string | null;
}
export interface EmailEvent { id: number; message_id: number | null; event: string; actor: UserRef | null; data: Record<string, unknown>; created_at: string }
export interface Mailbox {
  id: number; provider: "mock" | "gmail" | "microsoft"; email_address: string; display_name: string; is_shared: boolean;
  owner: UserRef | null; authorization_note: string; status: "pending_approval" | "active" | "needs_reauth" | "disabled";
  sending_enabled: boolean; daily_send_limit: number; scopes: string[]; approved_at: string | null; last_sync_at: string | null;
  last_error: string | null; effective_mode: "draft" | "send" | "none"; effective_mode_reason: string;
}
export interface OutreachSequence {
  id: number; lead_id: number; institution: { id: number; name: string }; contact_id: number; contact_name: string;
  contact_email: string | null; contact_designation: string | null; mailbox_id: number | null; use_case: string | null;
  use_case_label: string | null; status: "draft" | "active" | "stopped" | "completed"; stop_reason: string | null;
  stopped_at: string | null; max_attempts: number; spacing_days: number[]; is_demo: boolean; created_at: string;
}
export interface OutreachFact { id: string; category: string; level: string; text: string; excerpt?: string | null; source_url?: string | null; source_title?: string | null; published_at?: string | null }
export interface OutreachSequenceDetail extends OutreachSequence {
  facts: OutreachFact[]; messages: OutreachMessage[]; replies: EmailReply[]; events: EmailEvent[]; mailbox: Mailbox | null; contact_suppressed: boolean;
}
export interface WorkItem { message: OutreachMessage; sequence: OutreachSequence; due_at: string | null }
export interface ReplyItem { reply: EmailReply; sequence: OutreachSequence | null }
export interface OutreachOverview {
  drafts: number; scheduled: number; sent_7d: number; blocked: number; replies_attention: number; followups_due: number;
  live_sequences: number; sending_mode: "draft_only" | "send"; external_sending_allowed: boolean; writer: string; mailboxes_active: number;
}
export interface EligibleLead {
  lead_id: number; institution: { id: number; name: string; score: number | null; tier: string | null }; stage: LeadStage;
  contacts: { id: number; name: string; designation: string | null; email: string | null; problems: string[] }[];
}
export interface SuppressedView {
  consent_records: { id: number; contact_id: number | null; email: string | null; domain: string | null; status: string; channel: string; reason: string | null; source: string | null; recorded_at: string }[];
  bounced_contacts: { id: number; name: string; email: string | null; institution: string; status: string }[];
  stopped_sequences: OutreachSequence[];
}
export interface OutreachPolicy {
  sending_mode: "draft_only" | "send"; max_attempts: number; spacing_days: number[]; global_daily_limit: number;
  per_domain_daily_limit: number; max_live_per_institution: number; send_window: { start_hour: number; end_hour: number; timezone: string; weekdays_only: boolean };
  max_words_first: number; max_words_follow_up: number; ooo_postpone_days: number; sender_company: string; signature: string;
  opt_out_line: string; banned_phrases: string[]; restricted_terms: string[]; external_sending_allowed?: boolean;
}
export interface ContentItem { id: number; kind: "case_study" | "claim" | "cta" | "value_prop"; title: string; body: string; use_cases: string[]; status: "draft" | "approved" | "retired"; source_note: string | null; approved_at: string | null; is_demo: boolean; updated_at: string }
export interface EmailTemplateT { id: number; name: string; step: number; use_case: string | null; subject: string; body: string; status: "draft" | "approved" | "retired"; is_demo: boolean }
export interface MailProvider { key: string; name: string; available: boolean; note: string }
