"use client";

import { ArrowLeftIcon, CheckCircleIcon } from "@heroicons/react/24/outline";
import { useTranslations } from "next-intl";
import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../../lib/api";
import { defaultLocale, formatDateTime, isLocale } from "../../lib/locales";
import { isAudioMimeType, isImageMimeType } from "../../lib/media";
import {
  answerClarification,
  getReport,
  getTimeline,
  transitionReport,
  type AvailableTransition,
  type ReportDetail as ReportDetailData,
  type TimelineEntry,
} from "../../lib/reports";
import type { ReportStatus } from "../../lib/stateMachine";
import { createClient } from "../../lib/supabase/browser";
import { BottomNavigation } from "../navigation/BottomNavigation";
import { useReporterNavigation } from "../navigation/useReporterNavigation";
import { Banner } from "../ui/Banner";
import { SecondaryButton } from "../ui/Buttons";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { PhotoStrip } from "../ui/PhotoStrip";
import { StatusChip } from "../ui/StatusChip";
import { Timeline, type TimelineEvent } from "../ui/Timeline";
import { VoiceConfirmedTextarea } from "./VoiceConfirmedTextarea";
import { WorkflowNextStep } from "./WorkflowNextStep";

const transitionErrorKeys: Record<string, string> = {
  reason_required: "error.reason_required",
  illegal_transition: "error.illegal_transition",
  terminal_state: "error.terminal_state",
  actor_not_permitted: "error.actor_not_permitted",
  role_not_permitted: "error.role_not_permitted",
  unknown_event: "error.unknown_event",
  database_guard: "error.database_guard",
};

const clarificationErrorKeys: Record<string, string> = {
  clarification_actor_forbidden: "error.clarification_actor_forbidden",
  clarification_forbidden: "error.clarification_forbidden",
  clarification_answer_required: "error.clarification_answer_required",
  report_not_clarifying: "error.report_not_clarifying",
  clarification_not_found: "error.clarification_not_found",
  clarification_already_answered: "error.clarification_already_answered",
  clarification_round_invalid: "error.clarification_round_invalid",
  clarification_transcript_not_found: "error.clarification_transcript_not_found",
};

function actorKey(entry: TimelineEntry): string {
  if (entry.actor_type === "human" && entry.actor_role) {
    return `timeline.actor.${entry.actor_role}`;
  }
  return `timeline.actor.${entry.actor_type}`;
}

function receiptClause(value: string): string {
  return value
    .replace(/\s+/gu, " ")
    .trim()
    .replace(/[.!?;。！？；]+/gu, ",")
    .replace(/,+/gu, ",")
    .replace(/^,|,$/gu, "")
    .trim();
}

function lastTimelineEvent(entries: TimelineEntry[], event: string): TimelineEntry | undefined {
  return [...entries].reverse().find((entry) => entry.event === event);
}

function ReporterDetailShell({
  children,
  locale,
}: {
  children: ReactNode;
  locale: Parameters<typeof useReporterNavigation>[0];
}) {
  const navItems = useReporterNavigation(locale);

  return (
    <div className="mx-auto flex min-h-screen max-w-[430px] flex-col bg-bg text-ink">
      <main className="flex-1 px-5 py-10">{children}</main>
      <BottomNavigation items={navItems} activeHref={`/${locale}/reports`} />
    </div>
  );
}

export function ReportDetail({ id, requestedLocale }: { id: string; requestedLocale: string }) {
  const t = useTranslations();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const navItems = useReporterNavigation(locale);
  const [report, setReport] = useState<ReportDetailData | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [activeTransition, setActiveTransition] = useState<AvailableTransition | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [actionErrorKey, setActionErrorKey] = useState<string | null>(null);
  const [clarificationAnswers, setClarificationAnswers] = useState<Record<string, string>>({});
  const [clarificationTranscripts, setClarificationTranscripts] = useState<Record<string, string>>({});
  const [transcribingClarification, setTranscribingClarification] = useState<string | null>(null);
  const [answeringClarification, setAnsweringClarification] = useState<string | null>(null);
  const [clarificationErrorKey, setClarificationErrorKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadFailed(false);
    const {
      data: { session },
    } = await createClient().auth.getSession();
    if (!session) throw new Error("session_required");
    const [nextReport, nextTimeline] = await Promise.all([
      getReport(id, session.access_token),
      getTimeline(id, session.access_token),
    ]);
    setReport(nextReport);
    setTimeline(nextTimeline);
  }, [id]);

  useEffect(() => {
    let active = true;
    async function initialLoad() {
      try {
        await load();
      } catch {
        if (active) setLoadFailed(true);
      } finally {
        if (active) setLoading(false);
      }
    }
    void initialLoad();
    return () => {
      active = false;
    };
  }, [load]);

  async function applyTransition(transition: AvailableTransition) {
    setSubmitting(true);
    setActionErrorKey(null);
    try {
      const {
        data: { session },
      } = await createClient().auth.getSession();
      if (!session) throw new Error("session_required");
      await transitionReport(
        id,
        transition.target,
        session.access_token,
        transition.requires_reason ? reason.trim() : undefined,
      );
      setActiveTransition(null);
      setReason("");
      await load();
    } catch (error) {
      const code = error instanceof ApiError ? error.body.detail.code : "";
      setActionErrorKey(transitionErrorKeys[code] ?? "report.detail.actionFailed");
    } finally {
      setSubmitting(false);
    }
  }

  function chooseTransition(transition: AvailableTransition) {
    setActionErrorKey(null);
    if (transition.requires_reason) {
      setActiveTransition(transition);
      setReason("");
      return;
    }
    void applyTransition(transition);
  }

  async function submitClarification(clarificationId: string) {
    const answer = clarificationAnswers[clarificationId]?.trim() ?? "";
    if (!answer) return;
    setAnsweringClarification(clarificationId);
    setClarificationErrorKey(null);
    try {
      const {
        data: { session },
      } = await createClient().auth.getSession();
      if (!session) throw new Error("session_required");
      const transcriptId = clarificationTranscripts[clarificationId];
      if (transcriptId) {
        await answerClarification(
          id, clarificationId, answer, session.access_token, transcriptId,
        );
      } else {
        await answerClarification(id, clarificationId, answer, session.access_token);
      }
      setClarificationAnswers((current) => {
        const next = { ...current };
        delete next[clarificationId];
        return next;
      });
      setClarificationTranscripts((current) => {
        const next = { ...current };
        delete next[clarificationId];
        return next;
      });
      await load();
    } catch (error) {
      const code = error instanceof ApiError ? error.body.detail.code : "";
      setClarificationErrorKey(
        clarificationErrorKeys[code] ?? "report.clarification.failureDetail",
      );
    } finally {
      setAnsweringClarification(null);
    }
  }

  if (loading) {
    return (
      <ReporterDetailShell locale={locale}>
        <p className="text-base text-inkMuted" role="status">
          {t("report.detail.loading")}
        </p>
      </ReporterDetailShell>
    );
  }

  if (loadFailed || report === null) {
    return (
      <ReporterDetailShell locale={locale}>
        <Banner
          tone="warning"
          title={t("report.detail.loadFailedTitle")}
          detail={t("report.detail.loadFailedDetail")}
        />
      </ReporterDetailShell>
    );
  }

  const timelineEvents = timeline.map((entry) => ({
    id: entry.id,
    title: t(`timeline.event.${entry.event}`),
    detail: t(entry.actor_name ? "timeline.actorNamedAt" : "timeline.actorAt", {
      actor: t(actorKey(entry)),
      name: entry.actor_name ?? "",
      time: formatDateTime(entry.created_at, locale),
    }),
    note: entry.reason ? t("timeline.reason", { reason: entry.reason }) : undefined,
    status: entry.target ?? undefined,
  }));
  const receipt = report.closure_receipt;
  const submittedEvent = timeline.find((entry) => entry.event === "submit");
  const reviewedEvent = timeline.find((entry) =>
    entry.event === "approve_action" || entry.event === "approve_after_escalation"
  );
  const closedEvent = lastTimelineEvent(timeline, "verify_and_close");
  const passedVerification = receipt
    ? report.verifications.find((verification) => verification.id === receipt.verification_id)
    : undefined;
  const closureTimelineEvents: TimelineEvent[] = receipt
    ? [
        {
          id: "receipt-reported",
          title: t("receipt.timeline.reported"),
          detail: formatDateTime(
            submittedEvent?.created_at ?? report.submitted_at ?? report.created_at,
            locale,
          ),
        },
        {
          id: "receipt-reviewed",
          title: report.current_action
            ? t("receipt.timeline.reviewedAssigned", {
                assignee: report.current_action.assignee_name,
              })
            : t("receipt.timeline.reviewed"),
          detail: report.current_action
            ? t("receipt.timeline.reviewedDetail", {
                reviewedAt: formatDateTime(
                  reviewedEvent?.created_at ?? receipt.created_at,
                  locale,
                ),
                dueAt: formatDateTime(report.current_action.due_at, locale),
              })
            : formatDateTime(reviewedEvent?.created_at ?? receipt.created_at, locale),
        },
        ...report.verifications
          .filter((verification) => !verification.passed)
          .map((verification, index) => ({
            id: `receipt-returned-${verification.id}`,
            title: t("receipt.timeline.sentBack", { count: index + 1 }),
            detail: t("receipt.timeline.sentBackDetail", {
              time: formatDateTime(verification.created_at, locale),
              reviewer: verification.reviewer_name,
            }),
            note: verification.reason ?? undefined,
            state: "bad" as const,
          })),
        {
          id: "receipt-verified",
          title: t("receipt.timeline.fixedVerified"),
          detail: formatDateTime(passedVerification?.created_at ?? receipt.created_at, locale),
        },
        {
          id: "receipt-closed",
          title: t("receipt.timeline.closed"),
          detail: formatDateTime(
            closedEvent?.created_at ?? report.closed_at ?? receipt.created_at,
            locale,
          ),
        },
      ]
    : timelineEvents;
  const beforePhoto = receipt?.before_media_id
    ? report.media.find((media) => media.id === receipt.before_media_id)
    : undefined;
  const afterPhoto = receipt?.after_media_id
    ? report.media.find((media) => media.id === receipt.after_media_id)
    : undefined;
  const hasReceiptPhotoPair = Boolean(beforePhoto && afterPhoto);
  const pendingClarifications = report.clarifications.filter(
    (clarification) => clarification.answer === null,
  );
  const photoMedia = report.media.filter((item) => isImageMimeType(item.mime_type));
  const audioMedia = report.media.filter((item) => isAudioMimeType(item.mime_type));

  return (
    <div className="mx-auto flex min-h-screen max-w-[430px] flex-col bg-bg text-ink">
      <main className="flex-1 px-5 pb-10" data-report-id={report.id}>
      <header className="grid grid-cols-[44px_1fr_44px] items-center py-5">
        <Link className="grid min-h-11 min-w-11 place-items-center rounded-control" href={`/${locale}/reports`} aria-label={t("report.detail.back")}>
          <ArrowLeftIcon className="h-7 w-7" />
        </Link>
        <h1 className="text-center text-xl font-bold">{t("report.detail.title")}</h1>
        <span />
      </header>

      <div className="space-y-4">
        <Card className="space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm text-inkMuted">{t("report.detail.reference")}</p>
              <p className="text-xl font-bold">{report.human_ref}</p>
            </div>
            <StatusChip status={report.status} label={t(`status.${report.status}`)} />
          </div>
          <p className="text-sm text-inkMuted">
            {t("report.detail.createdAt", { time: formatDateTime(report.created_at, locale) })}
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
            <h2 className="text-xl font-bold">{t("report.detail.originalText")}</h2>
            <p className="mt-2 whitespace-pre-wrap text-base leading-7">{report.description_original}</p>
            <p className="mt-2 text-sm text-inkMuted">{t("report.detail.originalLanguage", { language: t(`locale.${report.lang_original}`) })}</p>
          </section>
          {report.description_en?.trim() && (
            <section className="border-t border-border pt-4">
              <h2 className="text-xl font-bold">{t("report.detail.englishText")}</h2>
              <p className="mt-2 whitespace-pre-wrap text-base leading-7">{report.description_en}</p>
            </section>
          )}
          {(report.location_text || report.activity || report.level_or_zone || report.grid_ref) && (
            <dl className="grid gap-3 border-t border-border pt-4 text-base">
              {report.location_text && <div><dt className="text-sm font-bold text-inkMuted">{t("report.detail.location")}</dt><dd>{report.location_text}</dd></div>}
              {report.activity && <div><dt className="text-sm font-bold text-inkMuted">{t("report.detail.activity")}</dt><dd>{report.activity}</dd></div>}
              {report.level_or_zone && <div><dt className="text-sm font-bold text-inkMuted">{t("report.detail.levelOrZone")}</dt><dd>{report.level_or_zone}</dd></div>}
              {report.grid_ref && <div><dt className="text-sm font-bold text-inkMuted">{t("report.detail.gridRef")}</dt><dd>{report.grid_ref}</dd></div>}
            </dl>
          )}
        </Card>

        {report.can_answer_clarifications && pendingClarifications.length > 0 && (
          <Card
            className="space-y-4 border-primary bg-primaryTint"
            data-testid="clarification-panel"
          >
            <div>
              <h2 className="text-xl font-bold text-ink">
                {t("report.clarification.title")}
              </h2>
              <p className="mt-1 text-sm text-inkMuted">
                {t("report.clarification.detail")}
              </p>
            </div>
            {pendingClarifications.map((clarification) => {
              const answer = clarificationAnswers[clarification.id] ?? "";
              const answering = answeringClarification === clarification.id;
              return (
                <section
                  className="space-y-3 rounded-control border border-border bg-surface p-4"
                  data-clarification-id={clarification.id}
                  key={clarification.id}
                >
                  <p className="text-base font-bold text-ink">{clarification.question}</p>
                  <VoiceConfirmedTextarea
                    id={`clarification-${clarification.id}`}
                    label={t("report.clarification.answerLabel")}
                    rows={3}
                    value={answer}
                    locale={locale}
                    reportId={id}
                    onTranscriptIdChange={(transcriptId) => setClarificationTranscripts((current) => {
                      const next = { ...current };
                      if (transcriptId) next[clarification.id] = transcriptId;
                      else delete next[clarification.id];
                      return next;
                    })}
                    onProcessingChange={(processing) => setTranscribingClarification(
                      processing ? clarification.id : null,
                    )}
                    onChange={(value) => setClarificationAnswers((current) => ({
                      ...current,
                      [clarification.id]: value,
                    }))}
                  />
                  <SecondaryButton
                    disabled={!answer.trim() || answeringClarification !== null
                      || transcribingClarification !== null}
                    label={answering
                      ? t("report.clarification.submitting")
                      : t("report.clarification.submit")}
                    onClick={() => void submitClarification(clarification.id)}
                  />
                </section>
              );
            })}
            {clarificationErrorKey && (
              <Banner
                tone="warning"
                title={t("report.clarification.failureTitle")}
                detail={t(clarificationErrorKey)}
              />
            )}
          </Card>
        )}

        <Card className="space-y-4">
          <h2 className="text-xl font-bold">{t("report.detail.timeline")}</h2>
          {closureTimelineEvents.length > 0 ? <Timeline events={closureTimelineEvents} /> : <p className="text-base text-inkMuted">{t("timeline.empty")}</p>}
        </Card>

        {receipt && (
          <section
            className="rounded-card border border-success bg-successSurface p-4 shadow-safe"
            aria-labelledby="closure-receipt-title"
          >
            <div className="mb-2.5 flex items-center gap-2 text-successStrong">
              <CheckCircleIcon className="h-6 w-6 flex-none text-success" />
              <h2 className="text-base font-bold" id="closure-receipt-title">
                {t("receipt.title")}
              </h2>
            </div>
            <p className="text-[15px] leading-6" data-testid="closure-receipt-summary">
              {t("receipt.summary", {
                action: receiptClause(receipt.action_text),
                notes: receiptClause(receipt.verification_notes),
                verifier: receipt.verified_by_name,
              })}
            </p>
            {hasReceiptPhotoPair && beforePhoto && afterPhoto && (
              <div className="mt-3.5 grid grid-cols-2 gap-3" data-testid="closure-receipt-photo-pair">
                <figure>
                  <figcaption className="mb-1.5 text-sm font-bold text-inkMuted">
                    {t("receipt.before")}
                  </figcaption>
                  <Image
                    className="aspect-[4/3] w-full rounded-tile object-cover"
                    src={beforePhoto.signed_url}
                    alt={t("receipt.beforeAlt")}
                    width={160}
                    height={120}
                    unoptimized
                  />
                </figure>
                <figure>
                  <figcaption className="mb-1.5 text-sm font-bold text-inkMuted">
                    {t("receipt.after")}
                  </figcaption>
                  <Image
                    className="aspect-[4/3] w-full rounded-tile object-cover"
                    src={afterPhoto.signed_url}
                    alt={t("receipt.afterAlt")}
                    width={160}
                    height={120}
                    unoptimized
                  />
                </figure>
              </div>
            )}
          </section>
        )}

        {actionErrorKey && <Banner tone="warning" title={t("report.detail.actionFailedTitle")} detail={t(actionErrorKey)} />}

        {report.available_transitions.length > 0 ? (
          <Card className="space-y-3">
            <h2 className="text-xl font-bold">{t("report.detail.actions")}</h2>
            {report.available_transitions.map((transition) => (
              <div className="space-y-3" key={transition.target}>
                {activeTransition?.target === transition.target ? (
                  <>
                    <Field
                      rows={3}
                      label={t("report.detail.reason")}
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                    />
                    <SecondaryButton
                      label={t(`action.${transition.event}`)}
                      disabled={reason.trim().length === 0 || submitting}
                      onClick={() => void applyTransition(transition)}
                    />
                  </>
                ) : (
                  <SecondaryButton
                    label={t(`action.${transition.event}`)}
                    disabled={submitting}
                    onClick={() => chooseTransition(transition)}
                  />
                )}
              </div>
            ))}
          </Card>
        ) : (
          <WorkflowNextStep report={report} locale={locale} audience="reporter" />
        )}
      </div>
      </main>
      <BottomNavigation
        items={navItems}
        activeHref={`/${locale}/reports`}
      />
    </div>
  );
}
