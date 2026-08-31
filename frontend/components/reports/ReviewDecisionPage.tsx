"use client";

import { ArrowLeftIcon } from "@heroicons/react/24/outline";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError } from "../../lib/api";
import {
  defaultLocale,
  formatDateTime,
  formatNumber,
  isLocale,
  locales,
} from "../../lib/locales";
import { isAudioMimeType, isImageMimeType } from "../../lib/media";
import {
  listTechnicians,
  type Technician,
} from "../../lib/profiles";
import {
  getReport,
  getTimeline,
  reviewReport,
  urgencyLevels,
  type AvailableTransition,
  type ReportDetail,
  type ReviewDecision,
  type TimelineEntry,
  type Urgency,
} from "../../lib/reports";
import { createClient } from "../../lib/supabase/browser";
import { SignOutButton } from "../auth/SignOutButton";
import { AiBlock } from "../ui/AiBlock";
import { Banner } from "../ui/Banner";
import {
  DestructiveButton,
  PrimaryButton,
  SecondaryButton,
} from "../ui/Buttons";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { LanguageSwitch } from "../ui/LanguageSwitch";
import { PhotoStrip } from "../ui/PhotoStrip";
import { StatusChip } from "../ui/StatusChip";
import { Timeline } from "../ui/Timeline";
import { WorkflowNextStep } from "./WorkflowNextStep";

type ReviewTransition = AvailableTransition & { review_decision: ReviewDecision };

const reviewErrorKeys: Record<string, string> = {
  reason_required: "error.reason_required",
  illegal_transition: "error.illegal_transition",
  terminal_state: "error.terminal_state",
  actor_not_permitted: "error.actor_not_permitted",
  role_not_permitted: "error.role_not_permitted",
  database_guard: "error.database_guard",
  assignment_required: "error.assignment_required",
  correction_reason_required: "error.correction_reason_required",
  review_target_mismatch: "error.review_target_mismatch",
  review_correction_invalid: "error.review_correction_invalid",
  review_actor_not_permitted: "error.review_actor_not_permitted",
  due_at_invalid: "error.due_at_invalid",
  assignee_not_responsible: "error.assignee_not_responsible",
  active_assignment_exists: "error.active_assignment_exists",
  report_not_found: "error.report_not_found",
};

const validationErrorKeys: Record<string, string> = {
  observed_facts_required: "review.draft.validation.observedFactsRequired",
  assumption_in_observed_facts: "review.draft.validation.assumptionInFacts",
  proposed_urgency_required: "review.draft.validation.urgencyRequired",
  confidence_below_threshold: "review.draft.validation.confidenceLow",
  escalation_reason_required: "review.draft.validation.escalationReasonRequired",
  suggested_action_citation_required: "review.draft.validation.actionCitationRequired",
  citation_source_unresolved: "review.draft.validation.citationSourceUnresolved",
  citation_quote_not_verbatim: "review.draft.validation.quoteNotVerbatim",
  suggested_action_not_quoted: "review.draft.validation.actionNotQuoted",
};

const missingInformationKeys: Record<
  string,
  { message: string; term: string }
> = {
  approved_work_at_height_procedure: {
    message: "review.draft.missingProcedure.workAtHeight",
    term: "term.workAtHeight",
  },
  approved_electrical_safety_procedure: {
    message: "review.draft.missingProcedure.electrical",
    term: "term.electricalSafety",
  },
  approved_site_safety_procedure: {
    message: "review.draft.missingProcedure.siteSafety",
    term: "term.siteSafety",
  },
};

type CorrectionField = "category" | "urgency" | "action";
type CorrectionDiff = {
  field: CorrectionField;
  before: string;
  after: string;
};

function isReviewTransition(
  transition: AvailableTransition,
): transition is ReviewTransition {
  return transition.review_decision !== undefined;
}

function actorKey(entry: TimelineEntry): string {
  if (entry.actor_type === "human" && entry.actor_role) {
    return `timeline.actor.${entry.actor_role}`;
  }
  return `timeline.actor.${entry.actor_type}`;
}

function DecisionButton({
  decision,
  label,
  disabled,
  onClick,
  type = "button",
}: {
  decision: ReviewDecision;
  label: string;
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
}) {
  const props = { disabled, label, onClick, type };
  if (decision === "approve") return <PrimaryButton {...props} />;
  if (decision === "reject") return <DestructiveButton {...props} />;
  return <SecondaryButton {...props} />;
}

export function ReviewDecisionPage({
  id,
  requestedLocale,
}: {
  id: string;
  requestedLocale: string;
}) {
  // Reviewer decisions and reasons are deliberate accountable acts. Keep every
  // field on this surface typed-only; do not add VoiceConfirmedTextarea here.
  const t = useTranslations();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [active, setActive] = useState<ReviewTransition | null>(null);
  const [reason, setReason] = useState("");
  const [correctedCategory, setCorrectedCategory] = useState("");
  const [correctedUrgency, setCorrectedUrgency] = useState<Urgency | "">("");
  const [correctedAction, setCorrectedAction] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");
  const [assigneeId, setAssigneeId] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [techniciansLoading, setTechniciansLoading] = useState(true);
  const [techniciansLoadFailed, setTechniciansLoadFailed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirmingApproval, setConfirmingApproval] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    const {
      data: { session },
    } = await createClient().auth.getSession();
    if (!session) throw new Error("session_required");
    const [nextReport, nextTimeline] = await Promise.all([
      getReport(id, session.access_token, locale),
      getTimeline(id, session.access_token),
    ]);
    setReport(nextReport);
    setTimeline(nextTimeline);
  }, [id, locale]);

  const loadTechnicianChoices = useCallback(async () => {
    setTechniciansLoading(true);
    setTechniciansLoadFailed(false);
    try {
      const {
        data: { session },
      } = await createClient().auth.getSession();
      if (!session) throw new Error("session_required");
      setTechnicians(await listTechnicians(session.access_token));
    } catch {
      setTechniciansLoadFailed(true);
    } finally {
      setTechniciansLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    async function initialLoad() {
      try {
        await load();
      } catch {
        if (mounted) setLoadFailed(true);
      } finally {
        if (mounted) setLoading(false);
      }
    }
    void initialLoad();
    void loadTechnicianChoices();
    return () => {
      mounted = false;
    };
  }, [load, loadTechnicianChoices]);

  function choose(transition: ReviewTransition) {
    const draft = report?.latest_draft;
    setActive(transition);
    setReason("");
    setCorrectedCategory(draft?.proposed_category ?? "");
    setCorrectedUrgency(draft?.proposed_urgency ?? "");
    setCorrectedAction(draft?.suggested_action ?? "");
    setCorrectionReason("");
    setAssigneeId("");
    setDueAt("");
    setConfirmingApproval(false);
    setErrorKey(null);
    setSaved(false);
  }

  const baselineCategory = report?.latest_draft?.proposed_category?.trim() ?? "";
  const baselineUrgency = report?.latest_draft?.proposed_urgency ?? "";
  const baselineAction = report?.latest_draft?.suggested_action?.trim() ?? "";
  const categoryValue = correctedCategory.trim();
  const actionValue = correctedAction.trim();
  const categoryChanged = Boolean(categoryValue && categoryValue !== baselineCategory);
  const urgencyChanged = Boolean(correctedUrgency && correctedUrgency !== baselineUrgency);
  const actionChanged = Boolean(actionValue && actionValue !== baselineAction);
  const correctionDiffs: CorrectionDiff[] = [
    ...(categoryChanged
      ? [{ field: "category" as const, before: baselineCategory, after: categoryValue }]
      : []),
    ...(urgencyChanged
      ? [{ field: "urgency" as const, before: baselineUrgency, after: correctedUrgency }]
      : []),
    ...(actionChanged
      ? [{ field: "action" as const, before: baselineAction, after: actionValue }]
      : []),
  ];
  const hasCorrections = correctionDiffs.length > 0;
  const reasonReady = !active?.requires_reason || reason.trim().length > 0;
  const correctionReady = !hasCorrections || correctionReason.trim().length > 0;
  const assignmentReady =
    active?.review_decision !== "approve" ||
    Boolean(actionValue && assigneeId.trim() && dueAt);
  const ready = Boolean(active && reasonReady && correctionReady && assignmentReady);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || !ready) return;
    if (active.review_decision === "approve" && !confirmingApproval) {
      setConfirmingApproval(true);
      return;
    }
    setSubmitting(true);
    setErrorKey(null);
    setSaved(false);
    try {
      const {
        data: { session },
      } = await createClient().auth.getSession();
      if (!session) throw new Error("session_required");
      const dueAtIso = dueAt ? new Date(dueAt).toISOString() : undefined;
      await reviewReport(
        id,
        {
          decision: active.review_decision,
          target: active.target,
          reason: active.requires_reason ? reason.trim() : undefined,
          corrected_category: categoryChanged ? categoryValue : undefined,
          corrected_urgency: urgencyChanged ? correctedUrgency || undefined : undefined,
          corrected_action: actionChanged ? actionValue : undefined,
          correction_reason: hasCorrections
            ? correctionReason.trim()
            : undefined,
          assignee_id:
            active.review_decision === "approve"
              ? assigneeId.trim()
              : undefined,
          due_at:
            active.review_decision === "approve" ? dueAtIso : undefined,
        },
        session.access_token,
      );
      setActive(null);
      setConfirmingApproval(false);
      setSaved(true);
      await load();
    } catch (error) {
      const code = error instanceof ApiError ? error.body.detail.code : "";
      setErrorKey(reviewErrorKeys[code] ?? "review.detail.submitFailedDetail");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="mx-auto min-h-screen max-w-[430px] bg-bg px-5 py-10 text-ink">
        <p className="text-base text-inkMuted" role="status">
          {t("review.detail.loading")}
        </p>
      </main>
    );
  }

  if (loadFailed || report === null) {
    return (
      <main className="mx-auto min-h-screen max-w-[430px] bg-bg px-5 py-10 text-ink">
        <Banner
          tone="warning"
          title={t("review.detail.loadFailedTitle")}
          detail={t("review.detail.loadFailedDetail")}
        />
      </main>
    );
  }

  const decisions = report.available_transitions.filter(isReviewTransition);
  const photoMedia = report.media.filter((item) => isImageMimeType(item.mime_type));
  const audioMedia = report.media.filter((item) => isAudioMimeType(item.mime_type));
  const timelineEvents = timeline.map((entry) => ({
    id: entry.id,
    title: t(`timeline.event.${entry.event}`),
    detail: t(entry.actor_name ? "timeline.actorNamedAt" : "timeline.actorAt", {
      actor: t(actorKey(entry)),
      name: entry.actor_name ?? "",
      time: formatDateTime(entry.created_at, locale),
    }),
    note: entry.reason
      ? t("timeline.reason", { reason: entry.reason })
      : undefined,
    status: entry.target ?? undefined,
  }));
  const correctionValue = (diff: CorrectionDiff, value: string) => {
    if (!value) return t("review.detail.diffNotSet");
    return diff.field === "urgency" ? t(`urgency.${value}`) : value;
  };
  const selectedTechnician = technicians.find(
    (technician) => technician.id === assigneeId,
  );

  return (
    <main className="mx-auto min-h-screen max-w-[430px] bg-bg px-5 pb-10 text-ink">
      <header className="grid grid-cols-[44px_1fr_auto] items-center gap-2 py-5">
        <Link
          className="grid min-h-11 min-w-11 place-items-center rounded-control"
          href={`/${locale}/review`}
          aria-label={t("review.detail.back")}
        >
          <ArrowLeftIcon className="h-7 w-7" />
        </Link>
        <h1 className="min-w-0 truncate text-center text-xl font-bold">
          {report.human_ref}
        </h1>
        <div className="flex items-center gap-2">
          <LanguageSwitch
            current={locale}
            label={t("app.language")}
            options={[
              { value: locales[0], label: t("app.languageEnglish") },
              { value: locales[1], label: t("app.languageChinese") },
            ]}
          />
          <SignOutButton variant="icon" />
        </div>
      </header>

      <div className="space-y-4">
        <Card className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-bold text-inkMuted">
                {t("review.detail.report")}
              </p>
              <p className="mt-1 text-sm text-inkMuted">
                {t("report.detail.createdAt", {
                  time: formatDateTime(report.created_at, locale),
                })}
              </p>
            </div>
            <StatusChip
              status={report.status}
              label={t(`status.${report.status}`)}
            />
          </div>
          <p className="text-sm font-bold text-primaryStrong">
            {t("review.detail.urgency", {
              urgency: t(`urgency.${report.urgency}`),
            })}
          </p>
        </Card>

        {photoMedia.length > 0 && (
          <Card className="space-y-3">
            <h2 className="text-xl font-bold">{t("report.media.photos")}</h2>
            <PhotoStrip
              photos={photoMedia.map((item) => ({
                src: item.signed_url,
                alt: item.caption?.trim() || t("report.media.photoAlt"),
              }))}
            />
          </Card>
        )}

        {audioMedia.length > 0 && (
          <Card className="space-y-3">
            <h2 className="text-xl font-bold">{t("report.media.audio")}</h2>
            {audioMedia.map((item) => (
              <audio
                key={item.id}
                className="w-full"
                controls
                preload="metadata"
                src={item.signed_url}
              >
                {t("report.voice.playbackUnsupported")}
              </audio>
            ))}
          </Card>
        )}

        <Card className="space-y-4">
          <section>
            <h2 className="text-xl font-bold">
              {t(
                locale === "zh-CN"
                  ? "review.detail.localizedReport"
                  : "report.detail.originalText",
              )}
            </h2>
            <p className="mt-2 whitespace-pre-wrap text-base leading-7">
              {report.description_original}
            </p>
            {locale !== "zh-CN" && (
              <p className="mt-2 text-sm text-inkMuted">
                {t("report.detail.originalLanguage", {
                  language: t(`locale.${report.lang_original}`),
                })}
              </p>
            )}
          </section>
          {locale !== "zh-CN" && report.description_en?.trim() && (
            <section className="border-t border-border pt-4">
              <h2 className="text-xl font-bold">
                {t("report.detail.englishText")}
              </h2>
              <p className="mt-2 whitespace-pre-wrap text-base leading-7">
                {report.description_en}
              </p>
            </section>
          )}
          <dl className="grid gap-3 border-t border-border pt-4 text-base">
            {report.location_text && (
              <div>
                <dt className="text-sm font-bold text-inkMuted">
                  {t("report.detail.location")}
                </dt>
                <dd>{report.location_text}</dd>
              </div>
            )}
            {report.activity && (
              <div>
                <dt className="text-sm font-bold text-inkMuted">
                  {t("report.detail.activity")}
                </dt>
                <dd>{report.activity}</dd>
              </div>
            )}
            {report.level_or_zone && (
              <div>
                <dt className="text-sm font-bold text-inkMuted">
                  {t("report.detail.levelOrZone")}
                </dt>
                <dd>{report.level_or_zone}</dd>
              </div>
            )}
            {report.grid_ref && (
              <div>
                <dt className="text-sm font-bold text-inkMuted">
                  {t("report.detail.gridRef")}
                </dt>
                <dd>{report.grid_ref}</dd>
              </div>
            )}
          </dl>
        </Card>

        {report.current_action && (
          <Card className="space-y-2 border-successStrong bg-successSurface">
            <p className="text-sm font-bold text-successStrong">
              {t("review.detail.assignedTo")}
            </p>
            <p className="text-xl font-bold text-ink">
              {report.current_action.assignee_name}
            </p>
            <p className="text-sm text-inkMuted">
              {t("review.detail.assignedAction", {
                action: report.current_action.action_text,
              })}
            </p>
            <p className="text-sm text-inkMuted">
              {t("review.detail.assignedDue", {
                dueAt: formatDateTime(report.current_action.due_at, locale),
              })}
            </p>
          </Card>
        )}

        {report.latest_draft && (
          <AiBlock
            marker={t("review.draft.marker")}
            observedLabel={t("review.draft.observed")}
            assumptionLabel={t("review.draft.assumptionNotObserved")}
            missingLabel={t("review.draft.missing")}
            emptyLabel={t("review.draft.none")}
            observedFacts={report.latest_draft.observed_facts}
            assumptions={report.latest_draft.assumptions}
            missingInformation={report.latest_draft.missing_information.map(
              (item) => {
                const translated = missingInformationKeys[item];
                return translated
                  ? t(translated.message, { procedure: t(translated.term) })
                  : item;
              },
            )}
            validationTitle={t("review.draft.validationFailed")}
            validationErrors={report.latest_draft.validation_errors.map((code) =>
              t(validationErrorKeys[code] ?? "review.draft.validation.unknown"),
            )}
          >
            <dl className="mt-4 grid gap-3 border-t border-border pt-4 text-base">
              {report.latest_draft.proposed_category && (
                <div>
                  <dt className="text-sm font-bold text-inkMuted">
                    {t("review.draft.category")}
                  </dt>
                  <dd>{report.latest_draft.proposed_category}</dd>
                </div>
              )}
              {report.latest_draft.proposed_urgency && (
                <div>
                  <dt className="text-sm font-bold text-inkMuted">
                    {t("review.draft.urgency")}
                  </dt>
                  <dd>{t(`urgency.${report.latest_draft.proposed_urgency}`)}</dd>
                </div>
              )}
              {report.latest_draft.suggested_owner_role && (
                <div>
                  <dt className="text-sm font-bold text-inkMuted">
                    {t("review.draft.owner")}
                  </dt>
                  <dd>{report.latest_draft.suggested_owner_role}</dd>
                </div>
              )}
              {report.latest_draft.suggested_action && (
                <div>
                  <dt className="text-sm font-bold text-inkMuted">
                    {t("review.draft.action")}
                  </dt>
                  <dd>{report.latest_draft.suggested_action}</dd>
                </div>
              )}
              {report.latest_draft.confidence !== null && (
                <div>
                  <dt className="text-sm font-bold text-inkMuted">
                    {t("review.draft.confidence")}
                  </dt>
                  <dd>{formatNumber(report.latest_draft.confidence, locale)}</dd>
                </div>
              )}
            </dl>
            {report.latest_draft.citations.length > 0 && (
              <section className="mt-4 border-t border-border pt-4">
                <h3 className="text-base font-bold">
                  {t("review.draft.references")}
                </h3>
                <ol className="mt-3 space-y-3">
                  {report.latest_draft.citations.map((citation, index) => (
                    <li
                      className="rounded-control border border-border bg-surface p-3"
                      key={`${citation.document_id}-${citation.section ?? ""}-${citation.page ?? ""}-${index}`}
                    >
                      <p className="text-sm font-bold text-ink">
                        {t("review.draft.referenceDocument", {
                          docRef: citation.doc_ref,
                          revision: citation.revision,
                        })}
                      </p>
                      <p className="mt-1 flex flex-wrap gap-x-3 text-sm text-inkMuted">
                        {citation.section && (
                          <span>
                            {t("review.draft.referenceSection", {
                              section: citation.section,
                            })}
                          </span>
                        )}
                        {citation.page !== null && (
                          <span>
                            {t("review.draft.referencePage", {
                              page: citation.page,
                            })}
                          </span>
                        )}
                      </p>
                      <blockquote className="mt-3 border-l-2 border-primary pl-3 text-base leading-7 text-ink">
                        {citation.quote}
                      </blockquote>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </AiBlock>
        )}

        <Card className="space-y-4">
          <h2 className="text-xl font-bold">{t("report.detail.timeline")}</h2>
          {timelineEvents.length > 0 ? (
            <Timeline events={timelineEvents} />
          ) : (
            <p className="text-base text-inkMuted">{t("timeline.empty")}</p>
          )}
        </Card>

        {saved && (
          <Banner
            tone="info"
            title={t("review.detail.savedTitle")}
            detail={t("review.detail.savedDetail")}
          />
        )}
        {errorKey && (
          <Banner
            tone="warning"
            title={t("review.detail.submitFailedTitle")}
            detail={t(errorKey)}
          />
        )}

        {decisions.length === 0 ? (
          <WorkflowNextStep report={report} locale={locale} audience="reviewer" />
        ) : active ? (
          <Card className="space-y-4">
            <div>
              <p className="text-sm font-bold text-primaryStrong">
                {t("review.detail.decision")}
              </p>
              <h2 className="mt-1 text-xl font-bold">
                {t(`action.${active.event}`)}
              </h2>
            </div>
            <form className="space-y-4" onSubmit={submit}>
              {active.requires_reason && (
                <Field
                  rows={3}
                  label={t("review.detail.reason")}
                  value={reason}
                  onChange={(event) => {
                    setReason(event.target.value);
                    setConfirmingApproval(false);
                  }}
                  error={
                    reason.length > 0 && reason.trim().length === 0
                      ? t("error.reason_required")
                      : undefined
                  }
                />
              )}

              <details className="rounded-control border border-border bg-surfaceSunken p-4">
                <summary className="min-h-11 cursor-pointer text-base font-bold">
                  {t("review.detail.corrections")}
                </summary>
                <div className="space-y-4 pt-3">
                  <Field
                    label={t("review.detail.correctedCategory")}
                    value={correctedCategory}
                    onChange={(event) => {
                      setCorrectedCategory(event.target.value);
                      setConfirmingApproval(false);
                    }}
                  />
                  <label className="block text-sm font-bold text-inkMuted">
                    <span>{t("review.detail.correctedUrgency")}</span>
                    <select
                      className="mt-1 min-h-[52px] w-full rounded-control border border-border bg-surface px-4 text-base text-ink outline-none focus:border-primaryStrong focus:ring-2 focus:ring-primaryTint"
                      value={correctedUrgency}
                      onChange={(event) => {
                        setCorrectedUrgency(event.target.value as Urgency | "");
                        setConfirmingApproval(false);
                      }}
                    >
                      <option value="">{t("review.detail.noCorrection")}</option>
                      {urgencyLevels.map((urgency) => (
                        <option key={urgency} value={urgency}>
                          {t(`urgency.${urgency}`)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <Field
                    rows={3}
                    label={t("review.detail.correctedAction")}
                    value={correctedAction}
                    onChange={(event) => {
                      setCorrectedAction(event.target.value);
                      setConfirmingApproval(false);
                    }}
                  />
                  {hasCorrections && (
                    <Field
                      rows={3}
                      label={t("review.detail.correctionReason")}
                      value={correctionReason}
                      onChange={(event) => {
                        setCorrectionReason(event.target.value);
                        setConfirmingApproval(false);
                      }}
                      error={
                        correctionReason.length > 0 &&
                        correctionReason.trim().length === 0
                          ? t("error.correction_reason_required")
                          : undefined
                      }
                    />
                  )}
                </div>
              </details>

              {active.review_decision === "approve" && (
                <div className="space-y-4 rounded-control border border-border bg-successSurface p-4">
                  <p className="text-base font-bold text-successStrong">
                    {t("review.detail.assignment")}
                  </p>
                  <p className="text-sm text-inkMuted">
                    {t("review.detail.assignmentHelp")}
                  </p>
                  <label className="block text-sm font-bold text-inkMuted">
                    <span>{t("review.detail.assignee")}</span>
                    <select
                      className="mt-1 min-h-[52px] w-full rounded-control border border-border bg-surface px-4 text-base text-ink outline-none focus:border-primaryStrong focus:ring-2 focus:ring-primaryTint disabled:opacity-60"
                      disabled={techniciansLoading || techniciansLoadFailed}
                      value={assigneeId}
                      onChange={(event) => {
                        setAssigneeId(event.target.value);
                        setConfirmingApproval(false);
                      }}
                    >
                      <option value="">
                        {techniciansLoading
                          ? t("review.detail.assigneeLoading")
                          : t("review.detail.assigneePlaceholder")}
                      </option>
                      {technicians.map((technician) => (
                        <option key={technician.id} value={technician.id}>
                          {technician.display_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {techniciansLoadFailed && (
                    <Banner
                      tone="warning"
                      title={t("review.detail.assigneeLoadFailedTitle")}
                      detail={t("review.detail.assigneeLoadFailedDetail")}
                    />
                  )}
                  <Field
                    label={t("review.detail.dueAt")}
                    type="datetime-local"
                    value={dueAt}
                    onChange={(event) => {
                      setDueAt(event.target.value);
                      setConfirmingApproval(false);
                    }}
                  />
                </div>
              )}

              {active.review_decision === "approve" && confirmingApproval && (
                <section
                  className="rounded-control border border-primary bg-primaryTint p-4"
                  aria-label={t("review.detail.diffTitle")}
                >
                  <h3 className="text-base font-bold text-primaryStrong">
                    {t("review.detail.diffTitle")}
                  </h3>
                  <p className="mt-1 text-sm text-inkMuted">
                    {t("review.detail.diffHelp")}
                  </p>
                  {selectedTechnician && (
                    <p className="mt-3 rounded-control bg-surface p-3 text-base font-bold text-ink">
                      {t("review.detail.confirmAssignee", {
                        name: selectedTechnician.display_name,
                      })}
                    </p>
                  )}
                  {correctionDiffs.length === 0 ? (
                    <p className="mt-3 text-base font-bold text-ink">
                      {t("review.detail.diffNoChanges")}
                    </p>
                  ) : (
                    <dl className="mt-3 space-y-3">
                      {correctionDiffs.map((diff) => (
                        <div className="rounded-control bg-surface p-3" key={diff.field}>
                          <dt className="text-sm font-bold text-ink">
                            {t(`review.detail.diffField.${diff.field}`)}
                          </dt>
                          <dd className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                            <span className="text-inkMuted">
                              {t("review.detail.diffBefore", {
                                value: correctionValue(diff, diff.before),
                              })}
                            </span>
                            <span className="font-bold text-ink">
                              {t("review.detail.diffAfter", {
                                value: correctionValue(diff, diff.after),
                              })}
                            </span>
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </section>
              )}

              <div className="grid grid-cols-2 gap-3">
                <SecondaryButton
                  className="min-h-14"
                  label={t("review.detail.cancel")}
                  disabled={submitting}
                  onClick={() => {
                    setActive(null);
                    setConfirmingApproval(false);
                  }}
                  type="button"
                />
                <DecisionButton
                  decision={active.review_decision}
                  label={
                    submitting
                      ? t("review.detail.submitting")
                      : active.review_decision === "approve"
                        ? confirmingApproval
                          ? t("review.detail.confirmApproval")
                          : t("review.detail.reviewApproval")
                        : t(`action.${active.event}`)
                  }
                  disabled={!ready || submitting}
                  type="submit"
                />
              </div>
            </form>
          </Card>
        ) : (
          <Card className="space-y-3">
            <div>
              <h2 className="text-xl font-bold">
                {t("review.detail.actions")}
              </h2>
              <p className="mt-1 text-sm text-inkMuted">
                {t("review.detail.actionsHelp")}
              </p>
            </div>
            {decisions.map((transition) => (
              <DecisionButton
                decision={transition.review_decision}
                key={transition.event}
                label={t(`action.${transition.event}`)}
                onClick={() => choose(transition)}
              />
            ))}
          </Card>
        )}
      </div>
    </main>
  );
}
