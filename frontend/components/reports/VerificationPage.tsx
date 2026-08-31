"use client";

import {
  ArrowLeftIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  PhotoIcon,
} from "@heroicons/react/24/outline";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../../lib/api";
import {
  defaultLocale,
  formatDateTime,
  formatNumber,
  isLocale,
  locales,
} from "../../lib/locales";
import { mediaPhase } from "../../lib/media";
import {
  getReport,
  verifyReport,
  type ReportDetail,
  type VerificationRecord,
} from "../../lib/reports";
import { createClient } from "../../lib/supabase/browser";
import { SignOutButton } from "../auth/SignOutButton";
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
import { WorkflowNextStep } from "./WorkflowNextStep";

const checklistItems = [
  { id: "hazard_removed", message: "verification.checklist.hazardRemoved" },
  { id: "same_location", message: "verification.checklist.sameLocation" },
  { id: "no_new_hazard", message: "verification.checklist.noNewHazard" },
] as const;

type ChecklistId = (typeof checklistItems)[number]["id"];
type Decision = "pass" | "fail";

const verificationErrorKeys: Record<string, string> = {
  verification_actor_forbidden: "error.verification_actor_forbidden",
  verification_not_found: "error.verification_not_found",
  verification_action_not_found: "error.verification_action_not_found",
  verification_not_ready: "error.verification_not_ready",
  verification_assignment_changed: "error.verification_assignment_changed",
  verification_notes_required: "error.verification_notes_required",
  verification_reason_required: "error.verification_reason_required",
  verification_reason_too_vague: "error.verification_reason_too_vague",
  verification_due_at_required: "error.verification_due_at_required",
  verification_due_at_invalid: "error.verification_due_at_invalid",
  reason_required: "error.reason_required",
  actor_not_permitted: "error.actor_not_permitted",
  role_not_permitted: "error.role_not_permitted",
  illegal_transition: "error.illegal_transition",
  database_guard: "error.database_guard",
};

function isSpecificDeficiency(value: string): boolean {
  const normalized = value
    .trim()
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
  return Boolean(
    normalized &&
      ![
        "incomplete",
        "not complete",
        "not done",
        "not done yet",
        "still not done",
        "未完成",
        "没完成",
        "没做",
      ].includes(normalized),
  );
}

function selectedChecklist(record: VerificationRecord): ChecklistId[] {
  const storedChecklist = record.checklist;
  if (!storedChecklist || Array.isArray(storedChecklist)) return [];
  return checklistItems
    .filter((item) => storedChecklist[item.id] === true)
    .map((item) => item.id);
}

export function VerificationPage({
  id,
  requestedLocale,
}: {
  id: string;
  requestedLocale: string;
}) {
  // Verification notes are deliberate inspection evidence. Keep this surface
  // typed-only; do not add VoiceConfirmedTextarea here.
  const t = useTranslations();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [checklist, setChecklist] = useState<Record<ChecklistId, boolean>>({
    hazard_removed: false,
    same_location: false,
    no_new_hazard: false,
  });
  const [notes, setNotes] = useState("");
  const [reason, setReason] = useState("");
  const [newDueAt, setNewDueAt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    const {
      data: { session },
    } = await createClient().auth.getSession();
    if (!session) throw new Error("session_required");
    setReport(await getReport(id, session.access_token));
  }, [id]);

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
    return () => {
      mounted = false;
    };
  }, [load]);

  function choose(nextDecision: Decision) {
    setDecision(nextDecision);
    setReason("");
    setNewDueAt("");
    setErrorKey(null);
    setSaved(false);
  }

  const notesReady = notes.trim().length > 0;
  const checklistComplete = checklistItems.every((item) => checklist[item.id]);
  const failedReady =
    notesReady && isSpecificDeficiency(reason) && newDueAt.length > 0;
  const ready = decision === "pass" ? notesReady && checklistComplete : failedReady;

  async function submit() {
    if (!decision || !ready) return;
    setSubmitting(true);
    setSaved(false);
    setErrorKey(null);
    try {
      const {
        data: { session },
      } = await createClient().auth.getSession();
      if (!session) throw new Error("session_required");
      await verifyReport(
        id,
        {
          passed: decision === "pass",
          checklist,
          notes: notes.trim(),
          reason: decision === "fail" ? reason.trim() : undefined,
          new_due_at:
            decision === "fail" ? new Date(newDueAt).toISOString() : undefined,
        },
        session.access_token,
      );
      setDecision(null);
      setSaved(true);
      await load();
    } catch (error) {
      const code = error instanceof ApiError ? error.body.detail.code : "";
      setErrorKey(
        verificationErrorKeys[code] ?? "verification.submit.failureDetail",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <main className="mx-auto min-h-screen max-w-[430px] bg-bg px-5 py-10 text-ink">
        <p className="text-base text-inkMuted" role="status">
          {t("verification.loading")}
        </p>
      </main>
    );
  }

  if (loadFailed || report === null) {
    return (
      <main className="mx-auto min-h-screen max-w-[430px] bg-bg px-5 py-10 text-ink">
        <Banner
          tone="warning"
          title={t("verification.loadFailedTitle")}
          detail={t("verification.loadFailedDetail")}
        />
      </main>
    );
  }

  const action = report.current_action;
  const evidence = report.media.filter((item) => item.phase === mediaPhase.evidence);
  const passAvailable = report.available_transitions.some(
    (transition) => transition.event === "verify_and_close",
  );
  const failAvailable = report.available_transitions.some(
    (transition) => transition.event === "verification_failed",
  );
  const lastFailure = [...report.verifications]
    .reverse()
    .find((verification) => !verification.passed);

  return (
    <main className="mx-auto min-h-screen max-w-[430px] bg-bg px-5 pb-10 text-ink">
      <header className="grid grid-cols-[44px_1fr_auto] items-center gap-2 py-5">
        <Link
          className="grid min-h-11 min-w-11 place-items-center rounded-control"
          href={`/${locale}/review`}
          aria-label={t("verification.back")}
        >
          <ArrowLeftIcon className="h-7 w-7" />
        </Link>
        <div className="min-w-0 text-center">
          <h1 className="truncate text-xl font-bold">{report.human_ref}</h1>
          {action && (
            <p className="text-sm font-bold text-primaryStrong">
              {t("verification.attempt", {
                count: formatNumber(action.rework_count + 1, locale),
              })}
            </p>
          )}
        </div>
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
        {lastFailure && (
          <Banner
            tone="warning"
            title={t("verification.returned.title", {
              count: formatNumber(action?.rework_count ?? 0, locale),
            })}
            detail={t("verification.returned.detail", {
              date: formatDateTime(lastFailure.created_at, locale),
              reason: lastFailure.reason ?? t("verification.history.reasonMissing"),
            })}
          />
        )}

        <Card className="space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-bold uppercase tracking-wide text-primaryStrong">
                {t("verification.evidence.title")}
              </p>
              {action?.submitted_at && (
                <p className="mt-1 text-sm text-inkMuted">
                  {t("verification.evidence.submitted", {
                    date: formatDateTime(action.submitted_at, locale),
                    name: action.assignee_name,
                  })}
                </p>
              )}
            </div>
            <StatusChip
              status={report.status}
              label={t(`status.${report.status}`)}
            />
          </div>
          {evidence.length > 0 ? (
            <PhotoStrip
              photos={evidence.map((item) => ({
                src: item.signed_url,
                alt: item.caption?.trim() || t("verification.evidence.photoAlt"),
              }))}
            />
          ) : (
            <div className="flex min-h-20 items-center gap-3 rounded-control bg-surfaceSunken p-4 text-inkMuted">
              <PhotoIcon className="h-7 w-7 shrink-0" />
              <span className="text-sm">{t("verification.evidence.noPhotos")}</span>
            </div>
          )}
          {action?.completed_note && (
            <p className="whitespace-pre-wrap rounded-control bg-surfaceSunken p-4 text-base leading-7">
              {action.completed_note}
            </p>
          )}
          {action && (
            <div className="border-t border-border pt-3">
              <p className="text-sm font-bold text-inkMuted">
                {t("verification.action.label")}
              </p>
              <p className="mt-1 text-base">{action.action_text}</p>
            </div>
          )}
        </Card>

        {report.verifications.length > 0 && (
          <Card className="space-y-4">
            <h2 className="text-lg font-bold">{t("verification.history.title")}</h2>
            {[...report.verifications].reverse().map((verification) => {
              const selected = selectedChecklist(verification);
              return (
                <article
                  className="space-y-2 border-b border-border pb-4 last:border-0 last:pb-0"
                  key={verification.id}
                >
                  <div className="flex items-start gap-2">
                    {verification.passed ? (
                      <CheckCircleIcon className="h-6 w-6 shrink-0 text-success" />
                    ) : (
                      <ExclamationTriangleIcon className="h-6 w-6 shrink-0 text-danger" />
                    )}
                    <div>
                      <h3 className="font-bold">
                        {t(
                          verification.passed
                            ? "verification.history.passed"
                            : "verification.history.failed",
                        )}
                      </h3>
                      <p className="text-sm text-inkMuted">
                        {t("verification.history.byAt", {
                          name: verification.reviewer_name,
                          date: formatDateTime(verification.created_at, locale),
                        })}
                      </p>
                    </div>
                  </div>
                  {verification.reason && (
                    <p className="rounded-control bg-dangerTint p-3 text-sm font-bold text-dangerStrong">
                      {verification.reason}
                    </p>
                  )}
                  {verification.notes && (
                    <p className="whitespace-pre-wrap text-sm text-inkMuted">
                      {verification.notes}
                    </p>
                  )}
                  {selected.length > 0 && (
                    <ul className="space-y-1 text-sm text-inkMuted">
                      {selected.map((item) => (
                        <li key={item}>
                          {t("verification.history.checked", {
                            item: t(
                              checklistItems.find((entry) => entry.id === item)!
                                .message,
                            ),
                          })}
                        </li>
                      ))}
                    </ul>
                  )}
                  {verification.new_due_at && (
                    <p className="text-sm font-bold text-inkMuted">
                      {t("verification.history.newDue", {
                        date: formatDateTime(verification.new_due_at, locale),
                      })}
                    </p>
                  )}
                </article>
              );
            })}
          </Card>
        )}

        {saved && (
          <Banner
            tone="info"
            title={t("verification.submit.successTitle")}
            detail={t("verification.submit.successDetail")}
          />
        )}

        {(passAvailable || failAvailable) && action ? (
          <Card className="space-y-4">
            <h2 className="text-lg font-bold">{t("verification.checklist.title")}</h2>
            <div className="space-y-2">
              {checklistItems.map((item) => (
                <label
                  className="flex min-h-11 items-center justify-between gap-3 rounded-control border border-border px-3 text-base"
                  key={item.id}
                >
                  <span>{t(item.message)}</span>
                  <input
                    checked={checklist[item.id]}
                    className="h-6 w-6 accent-primaryStrong"
                    onChange={(event) =>
                      setChecklist((current) => ({
                        ...current,
                        [item.id]: event.target.checked,
                      }))
                    }
                    type="checkbox"
                  />
                </label>
              ))}
            </div>
            <Field
              label={t("verification.notes.label")}
              placeholder={t("verification.notes.placeholder")}
              rows={3}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />

            {decision === "fail" && (
              <div className="space-y-3 rounded-card border border-danger bg-dangerTint p-4">
                <Field
                  label={t("verification.failure.reasonLabel")}
                  placeholder={t("verification.failure.reasonPlaceholder")}
                  error={
                    reason.trim() && !isSpecificDeficiency(reason)
                      ? t("error.verification_reason_too_vague")
                      : undefined
                  }
                  rows={4}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
                <Field
                  label={t("verification.failure.newDueLabel")}
                  type="datetime-local"
                  value={newDueAt}
                  onChange={(event) => setNewDueAt(event.target.value)}
                />
                <p className="text-sm text-dangerStrong">
                  {t("verification.failure.help")}
                </p>
              </div>
            )}

            {errorKey && (
              <Banner
                tone="warning"
                title={t("verification.submit.failureTitle")}
                detail={t(errorKey)}
              />
            )}
            {decision === null ? (
              <div className="space-y-3">
                {passAvailable && (
                  <PrimaryButton
                    label={t("action.verify_and_close")}
                    onClick={() => choose("pass")}
                  />
                )}
                {failAvailable && (
                  <SecondaryButton
                    className="text-dangerStrong"
                    label={t("action.verification_failed")}
                    onClick={() => choose("fail")}
                  />
                )}
              </div>
            ) : (
              <div className="space-y-3">
                {decision === "pass" ? (
                  <PrimaryButton
                    label={
                      submitting
                        ? t("verification.submit.saving")
                        : t("action.verify_and_close")
                    }
                    disabled={!ready || submitting}
                    onClick={() => void submit()}
                  />
                ) : (
                  <DestructiveButton
                    label={
                      submitting
                        ? t("verification.submit.saving")
                        : t("action.verification_failed")
                    }
                    disabled={!ready || submitting}
                    onClick={() => void submit()}
                  />
                )}
                <SecondaryButton
                  label={t("verification.cancel")}
                  disabled={submitting}
                  onClick={() => setDecision(null)}
                />
              </div>
            )}
          </Card>
        ) : (
          <WorkflowNextStep report={report} locale={locale} audience="reviewer" />
        )}
      </div>
    </main>
  );
}
