"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

import type { Locale } from "../../lib/locales";
import {
  getLessonDraftStatus,
  startLessonDraft,
  type LessonDraftRunStatus,
  type ReportDetail,
} from "../../lib/reports";
import { createClient } from "../../lib/supabase/browser";
import { Banner } from "../ui/Banner";

type Audience = "reporter" | "reviewer";

const ownerByStatus = {
  draft: "reporter",
  submitted: "ai",
  clarifying: "reporter",
  ai_drafted: "system",
  under_review: "reviewer",
  rejected: "none",
  info_requested: "reporter",
  escalated: "reviewer",
  action_assigned: "technician",
  action_submitted: "reviewer",
  verified_closed: "ai",
  lesson_drafted: "reviewer",
  lesson_published: "none",
} as const;

export function WorkflowNextStep({
  report,
  locale,
  audience,
}: {
  report: ReportDetail;
  locale: Locale;
  audience: Audience;
}) {
  const t = useTranslations();
  const [draftStatus, setDraftStatus] = useState<LessonDraftRunStatus["status"]>("idle");
  const ownerKey = ownerByStatus[report.status];
  const owner = report.status === "action_assigned" && report.current_action
    ? t("workflow.next.namedTechnician", {
        name: report.current_action.assignee_name,
        role: t("workflow.owner.technician"),
      })
    : t(`workflow.owner.${ownerKey}`);
  const complete = ownerKey === "none";
  const briefingHref = report.current_briefing
    ? `/${locale}/briefings/${report.current_briefing.id}`
    : `/${locale}/briefings`;
  const showBriefingAction = audience === "reviewer" && report.status === "lesson_drafted";
  const showLessonRetry = audience === "reviewer" && report.status === "verified_closed";

  useEffect(() => {
    if (!showLessonRetry) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const { data: { session } } = await createClient().auth.getSession();
        if (!session) throw new Error("session_required");
        const result = await getLessonDraftStatus(report.id, session.access_token);
        if (cancelled) return;
        setDraftStatus(result.status);
        if (result.status === "succeeded") {
          window.location.assign(
            result.briefing_id
              ? `/${locale}/briefings/${result.briefing_id}`
              : `/${locale}/briefings`,
          );
          return;
        }
        if (result.status === "queued" || result.status === "running") {
          timeout = setTimeout(() => void poll(), 1500);
        }
      } catch {
        if (!cancelled) setDraftStatus("failed");
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timeout) clearTimeout(timeout);
    };
  }, [draftStatus, locale, report.id, showLessonRetry]);

  async function restartLessonDraft() {
    setDraftStatus("queued");
    try {
      const { data: { session } } = await createClient().auth.getSession();
      if (!session) throw new Error("session_required");
      const result = await startLessonDraft(report.id, session.access_token);
      setDraftStatus(result.status);
    } catch {
      setDraftStatus("failed");
    }
  }

  return (
    <Banner
      tone="info"
      title={complete ? t("workflow.next.completeTitle") : t("workflow.next.title", { owner })}
      detail={t(`workflow.next.${report.status}`)}
    >
      {showBriefingAction && (
        <Link
          className="mt-3 flex min-h-11 items-center justify-center rounded-control bg-primary px-4 text-base font-bold text-ink-inverse"
          href={briefingHref}
        >
          {t("workflow.next.reviewLesson")}
        </Link>
      )}
      {showLessonRetry && (draftStatus === "queued" || draftStatus === "running") && (
        <div className="mt-3" role="progressbar" aria-valuetext={t("workflow.next.draftingProgress")}>
          <div className="h-2 overflow-hidden rounded-chip bg-surface">
            <div className="h-full w-2/3 animate-pulse rounded-chip bg-primary" />
          </div>
          <span className="mt-2 block text-sm font-bold">
            {draftStatus === "queued"
              ? t("workflow.next.draftingQueued")
              : t("workflow.next.draftingProgress")}
          </span>
        </div>
      )}
      {showLessonRetry && (draftStatus === "idle" || draftStatus === "failed") && (
        <button
          className="mt-3 min-h-11 w-full rounded-control border border-primaryStrong bg-surface px-4 text-base font-bold text-primaryStrong disabled:opacity-60"
          onClick={() => void restartLessonDraft()}
          type="button"
        >
          {t("workflow.next.retryLesson")}
        </button>
      )}
      {draftStatus === "failed" && (
        <span className="mt-3 block text-sm font-bold">{t("workflow.next.lessonRetryFailed")}</span>
      )}
    </Banner>
  );
}
