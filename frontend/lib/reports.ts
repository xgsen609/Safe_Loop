import { apiFetch } from "./api";
import type { Locale } from "./locales";
import {
  uploadReportPhoto,
  type MediaPhase,
  type ReportPhotoUpload,
} from "./media";
import { reportStatus, type ReportStatus } from "./stateMachine";

export const urgencyLevels = ["low", "medium", "high", "critical"] as const;
export type Urgency = (typeof urgencyLevels)[number];

export type NewReportInput = {
  description_original: string;
  lang_original: Locale;
  location_text: string;
  activity: string;
  level_or_zone: string | null;
  grid_ref: string | null;
  is_confidential: boolean;
};

type CreatedReport = { id: string };
export type SubmittedReport = { id: string; human_ref: string; status: typeof reportStatus.submitted };

export type ReportMedia = {
  id: string;
  storage_path: string;
  mime_type: string;
  phase: MediaPhase;
  caption: string | null;
  corrective_action_id?: string | null;
  retention_until?: string | null;
  created_at?: string;
  signed_url: string;
  signed_url_expires_at: string;
};

export type AvailableTransition = {
  event: string;
  target: ReportStatus;
  requires_reason: boolean;
  review_decision?: ReviewDecision;
};

export const reviewDecisions = ["approve", "request_info", "escalate", "reject"] as const;
export type ReviewDecision = (typeof reviewDecisions)[number];

export type ReviewInput = {
  decision: ReviewDecision;
  target: ReportStatus;
  reason?: string;
  corrected_category?: string;
  corrected_urgency?: Urgency;
  corrected_action?: string;
  correction_reason?: string;
  assignee_id?: string;
  due_at?: string;
};

export type ReviewResult = {
  review_id: string;
  report_id: string;
  status: ReportStatus;
  assignment_id: string | null;
  corrective_action_id: string | null;
};

export type AiDraftCitation = {
  document_id: string;
  doc_ref: string;
  revision: string;
  section: string | null;
  page: number | null;
  quote: string;
};

export type AiDraft = {
  id: string;
  version: number;
  observed_facts: string[];
  assumptions: string[];
  missing_information: string[];
  proposed_category: string | null;
  proposed_urgency: Urgency | null;
  suggested_owner_role: string | null;
  suggested_action: string | null;
  confidence: number | null;
  needs_escalation: boolean;
  escalation_reason: string | null;
  citations: AiDraftCitation[];
  validation: "valid" | "invalid" | null;
  validation_errors: string[];
  created_at: string;
};

export type CorrectiveActionDetail = {
  id: string;
  assignment_id: string;
  assignee_id: string;
  assignee_name: string;
  assignment_active: boolean;
  action_text: string;
  status: "assigned" | "submitted" | "verified";
  rework_count: number;
  due_at: string;
  completed_note: string | null;
  submitted_at: string | null;
};

export type VerificationRecord = {
  id: string;
  corrective_action_id: string;
  reviewer_id: string;
  reviewer_name: string;
  passed: boolean;
  checklist: Record<string, unknown> | unknown[] | null;
  notes: string | null;
  reason: string | null;
  new_due_at: string | null;
  created_at: string;
};

export type ClosureReceipt = {
  id: string;
  verification_id: string;
  corrective_action_id: string;
  reporter_locale: Locale;
  action_text: string;
  verification_notes: string;
  verified_by_id: string;
  verified_by_name: string;
  before_media_id: string | null;
  after_media_id: string | null;
  created_at: string;
};

export type VerificationInput = {
  passed: boolean;
  checklist: Record<string, boolean> | null;
  notes: string;
  reason?: string;
  new_due_at?: string;
};

export type VerificationResult = {
  verification_id: string;
  report_id: string;
  status: ReportStatus;
  closed_at: string | null;
  corrective_action_id: string;
  action_status: "assigned" | "verified";
  rework_count: number;
  assignment_id: string;
  due_at: string;
};

export type Clarification = {
  id: string;
  report_id: string;
  round: number;
  gap: string;
  question: string;
  answer: string | null;
  answered_at: string | null;
  created_at: string;
};

export type ReportDetail = {
  id: string;
  human_ref: string;
  status: ReportStatus;
  urgency: Urgency;
  lang_original: Locale;
  description_original: string;
  description_en: string | null;
  location_text: string | null;
  activity: string | null;
  level_or_zone: string | null;
  grid_ref: string | null;
  submitted_at: string | null;
  closed_at: string | null;
  created_at: string;
  clarify_rounds: number;
  clarifications: Clarification[];
  can_answer_clarifications: boolean;
  media: ReportMedia[];
  latest_draft: AiDraft | null;
  current_action: CorrectiveActionDetail | null;
  verifications: VerificationRecord[];
  closure_receipt: ClosureReceipt | null;
  current_briefing?: {
    id: string;
    version: number;
    status: "draft" | "published";
    created_at: string;
    approved_at: string | null;
  } | null;
  available_transitions: AvailableTransition[];
};

export type ReportListItem = {
  id: string;
  human_ref: string;
  status: ReportStatus;
  urgency: Urgency;
  summary: string;
  location_text: string | null;
  created_at: string;
  thumbnail_caption: string | null;
  thumbnail_url: string | null;
  thumbnail_url_expires_at: string | null;
  action_id: string | null;
  action_text: string | null;
  action_status: "assigned" | "submitted" | "verified" | null;
  action_due_at: string | null;
  completed_note: string | null;
  action_submitted_at: string | null;
  rework_count: number;
  rework_attention: boolean;
  sent_back_unresolved: boolean;
  deficiency_reason: string | null;
  deficiency_notes: string | null;
  deficiency_created_at: string | null;
  deficiency_reviewer_name: string | null;
  previous_evidence: Array<{
    id: string;
    caption: string | null;
    created_at: string;
    signed_url: string | null;
    signed_url_expires_at: string | null;
  }>;
};

export type ReportListPage = {
  items: ReportListItem[];
  next_cursor: string | null;
  counts: {
    overdue: number;
    rework: number;
  };
};

export type ReportListFilters = {
  status?: ReportStatus;
  urgency?: Urgency;
  assignee?: string;
  needsManualTriage?: boolean;
  q?: string;
  cursor?: string;
  limit?: number;
  locale?: Locale;
};

export type TimelineEntry = {
  id: string;
  event: string;
  actor_type: "human" | "ai" | "system";
  actor_role: "reporter" | "reviewer" | "responsible" | "crew" | "admin" | null;
  actor_name?: string | null;
  source: ReportStatus | null;
  target: ReportStatus | null;
  reason: string | null;
  created_at: string;
};

export async function fileReport(
  input: NewReportInput,
  accessToken: string,
  photo?: ReportPhotoUpload,
  existingDraftId?: string,
  transcriptId?: string,
  audioMediaId?: string,
): Promise<SubmittedReport> {
  const created = existingDraftId
    ? await apiFetch<CreatedReport>(`/reports/${existingDraftId}`, accessToken, {
        method: "PATCH",
        body: JSON.stringify(input),
      })
    : await createReportDraft(input, accessToken);
  if (photo) {
    await uploadReportPhoto({
      ...photo,
      reportId: created.id,
      accessToken,
    });
  }
  return apiFetch<SubmittedReport>(`/reports/${created.id}/transition`, accessToken, {
    method: "POST",
    body: JSON.stringify({
      target: reportStatus.submitted,
      confirmed_text: input.description_original,
      transcript_id: transcriptId,
      audio_media_id: audioMediaId,
    }),
  });
}

export function createReportDraft(
  input: NewReportInput,
  accessToken: string,
): Promise<CreatedReport> {
  return apiFetch<CreatedReport>("/reports", accessToken, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getReport(
  reportId: string,
  accessToken: string,
  locale?: Locale,
): Promise<ReportDetail> {
  const query = locale ? `?locale=${encodeURIComponent(locale)}` : "";
  return apiFetch<ReportDetail>(`/reports/${reportId}${query}`, accessToken);
}

export function listReports(filters: ReportListFilters, accessToken: string): Promise<ReportListPage> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.urgency) params.set("urgency", filters.urgency);
  if (filters.assignee) params.set("assignee", filters.assignee);
  if (filters.needsManualTriage) params.set("needs_manual_triage", "true");
  if (filters.q?.trim()) params.set("q", filters.q.trim());
  if (filters.cursor) params.set("cursor", filters.cursor);
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.locale) params.set("locale", filters.locale);
  const query = params.toString();
  return apiFetch<ReportListPage>(`/reports${query ? `?${query}` : ""}`, accessToken);
}

export function getTimeline(reportId: string, accessToken: string): Promise<TimelineEntry[]> {
  return apiFetch<TimelineEntry[]>(`/reports/${reportId}/timeline`, accessToken);
}

export function answerClarification(
  reportId: string,
  clarificationId: string,
  answer: string,
  accessToken: string,
  transcriptId?: string,
): Promise<{ id: string; report_id: string; answered_at: string; round_complete: boolean }> {
  return apiFetch(
    `/reports/${reportId}/clarifications/${clarificationId}/answer`,
    accessToken,
    {
      method: "POST",
      body: JSON.stringify({ answer, transcript_id: transcriptId }),
    },
  );
}

export function transitionReport(
  reportId: string,
  target: ReportStatus,
  accessToken: string,
  reason?: string,
): Promise<{ id: string; status: ReportStatus }> {
  return apiFetch<{ id: string; status: ReportStatus }>(
    `/reports/${reportId}/transition`,
    accessToken,
    {
      method: "POST",
      body: JSON.stringify({ target, reason }),
    },
  );
}

export function reviewReport(
  reportId: string,
  input: ReviewInput,
  accessToken: string,
): Promise<ReviewResult> {
  return apiFetch<ReviewResult>(`/reports/${reportId}/review`, accessToken, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function verifyReport(
  reportId: string,
  input: VerificationInput,
  accessToken: string,
): Promise<VerificationResult> {
  return apiFetch<VerificationResult>(`/reports/${reportId}/verify`, accessToken, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function startLessonDraft(
  reportId: string,
  accessToken: string,
): Promise<{ report_id: string; status: "queued" | "running" }> {
  return apiFetch(`/reports/${reportId}/lesson-draft`, accessToken, {
    method: "POST",
  });
}

export type LessonDraftRunStatus = {
  report_id: string;
  status: "idle" | "queued" | "running" | "succeeded" | "failed";
  started_at: string | null;
  finished_at: string | null;
  briefing_id: string | null;
};

export function getLessonDraftStatus(
  reportId: string,
  accessToken: string,
): Promise<LessonDraftRunStatus> {
  return apiFetch(`/reports/${reportId}/lesson-draft/status`, accessToken);
}
