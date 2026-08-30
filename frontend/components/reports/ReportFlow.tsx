"use client";

import {
  ArrowLeftIcon,
  CameraIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  MicrophoneIcon,
  PencilSquareIcon,
  PlusIcon,
  ShieldExclamationIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import Image from "next/image";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import React, { ChangeEvent, useEffect, useState } from "react";

import {
  getAlert,
  raiseAlert,
  reporterAlertCopyKey,
  type AlertItem,
} from "../../lib/alerts";
import {
  defaultLocale,
  formatDateTime,
  isLocale,
  localeCookieName,
  locales,
  type Locale,
} from "../../lib/locales";
import {
  createReportDraft,
  fileReport,
  type NewReportInput,
} from "../../lib/reports";
import { alertPollIntervalMs, siteEmergencyLine } from "../../lib/site";
import { createClient } from "../../lib/supabase/browser";
import { BottomNavigation } from "../navigation/BottomNavigation";
import { useReporterNavigation } from "../navigation/useReporterNavigation";
import { Banner } from "../ui/Banner";
import { PrimaryButton, SecondaryButton } from "../ui/Buttons";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { VoiceConfirmedTextarea } from "./VoiceConfirmedTextarea";

type FlowStep = "capture" | "question" | "urgent" | "review";
type CaptureMode = "voice" | "typed";
type DangerAnswer = "yes" | "no" | null;
type RequiredReportField = "description" | "location" | "activity";

type LocaleSwitchDraft = {
  locale: Locale;
  description: string;
  location: string;
  activity: string;
  levelOrZone: string;
  gridRef: string;
  detailsOpen: boolean;
  confidential: boolean;
  danger: DangerAnswer;
  draftId: string | null;
  photo: File | null;
  transcriptId: string | null;
  audioMediaId: string | null;
  missingFields: RequiredReportField[];
  captureMode: CaptureMode;
};

let localeSwitchDraft: LocaleSwitchDraft | null = null;

export function ReportFlow() {
  const t = useTranslations();
  const router = useRouter();
  const requestedLocale = useLocale();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const restoredDraft =
    localeSwitchDraft?.locale === locale ? localeSwitchDraft : null;
  const navItems = useReporterNavigation(locale);
  const [step, setStep] = useState<FlowStep>("capture");
  const [captureMode, setCaptureMode] = useState<CaptureMode>(
    restoredDraft?.captureMode ?? "voice",
  );
  const [description, setDescription] = useState(restoredDraft?.description ?? "");
  const [langOriginal, setLangOriginal] = useState<Locale>(locale);
  const [location, setLocation] = useState(restoredDraft?.location ?? "");
  const [activity, setActivity] = useState(restoredDraft?.activity ?? "");
  const [levelOrZone, setLevelOrZone] = useState(restoredDraft?.levelOrZone ?? "");
  const [gridRef, setGridRef] = useState(restoredDraft?.gridRef ?? "");
  const [detailsOpen, setDetailsOpen] = useState(restoredDraft?.detailsOpen ?? false);
  const [confidential, setConfidential] = useState(restoredDraft?.confidential ?? false);
  const [danger, setDanger] = useState<DangerAnswer>(restoredDraft?.danger ?? null);
  const [draftId, setDraftId] = useState<string | null>(restoredDraft?.draftId ?? null);
  const [urgentAlert, setUrgentAlert] = useState<AlertItem | null>(null);
  const [alerting, setAlerting] = useState(false);
  const [alertFailed, setAlertFailed] = useState(false);
  const [photo, setPhoto] = useState<File | null>(restoredDraft?.photo ?? null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [transcriptId, setTranscriptId] = useState<string | null>(restoredDraft?.transcriptId ?? null);
  const [audioMediaId, setAudioMediaId] = useState<string | null>(restoredDraft?.audioMediaId ?? null);
  const [voiceProcessing, setVoiceProcessing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);
  const [missingFields, setMissingFields] = useState<RequiredReportField[]>(
    restoredDraft?.missingFields ?? [],
  );
  const urgentAlertId = urgentAlert?.id;

  useEffect(() => {
    if (localeSwitchDraft?.locale === locale) localeSwitchDraft = null;
  }, [locale]);

  useEffect(() => {
    if (!photo) {
      setPhotoUrl(null);
      return;
    }

    const url = URL.createObjectURL(photo);
    setPhotoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  useEffect(() => {
    if (step !== "urgent" || urgentAlertId === undefined) return;
    const alertId = urgentAlertId;
    let active = true;

    async function refreshAlert() {
      try {
        const {
          data: { session },
        } = await createClient().auth.getSession();
        if (!session) return;
        const refreshed = await getAlert(alertId, session.access_token);
        if (active) setUrgentAlert(refreshed);
      } catch {
        // The last confirmed state remains visible until the next poll succeeds.
      }
    }

    const interval = window.setInterval(() => void refreshAlert(), alertPollIntervalMs);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [step, urgentAlertId]);

  function selectPhoto(event: ChangeEvent<HTMLInputElement>) {
    setPhoto(event.target.files?.[0] ?? null);
  }

  function switchReportLanguage(nextLocale: Locale) {
    localeSwitchDraft = {
      locale: nextLocale,
      description,
      location,
      activity,
      levelOrZone,
      gridRef,
      detailsOpen,
      confidential,
      danger,
      draftId,
      photo,
      transcriptId,
      audioMediaId,
      missingFields,
      captureMode,
    };
    document.cookie =
      `${localeCookieName}=${encodeURIComponent(nextLocale)}; ` +
      "Path=/; Max-Age=31536000; SameSite=Lax";
    router.replace(`/${nextLocale}/report/new`);
    router.refresh();
  }

  function reportInput(): NewReportInput {
    return {
      description_original: description.trim(),
      lang_original: langOriginal,
      location_text: location.trim(),
      activity: activity.trim(),
      level_or_zone: levelOrZone.trim() || null,
      grid_ref: gridRef.trim() || null,
      is_confidential: confidential,
    };
  }

  async function ensureVoiceDraft(): Promise<string> {
    if (draftId) return draftId;
    const {
      data: { session },
    } = await createClient().auth.getSession();
    if (!session) throw new Error("session_required");
    const draft = await createReportDraft(reportInput(), session.access_token);
    setDraftId(draft.id);
    return draft.id;
  }

  function clearMissingField(field: RequiredReportField, value: string) {
    if (!value.trim()) return;
    setMissingFields((current) => current.filter((item) => item !== field));
  }

  function validateRequiredFields(): boolean {
    const missing: RequiredReportField[] = [];
    if (!description.trim()) missing.push("description");
    if (!location.trim()) missing.push("location");
    if (!activity.trim()) missing.push("activity");
    setMissingFields(missing);
    return missing.length === 0;
  }

  function continueFromCapture() {
    const missing: RequiredReportField[] = [];
    if (!description.trim()) missing.push("description");
    if (!location.trim()) missing.push("location");
    setMissingFields(missing);
    if (missing.length === 0) setStep("question");
  }

  async function sendUrgentAlert() {
    setDanger("yes");
    setAlerting(true);
    setAlertFailed(false);
    try {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) throw new Error("session_required");

      let reportId = draftId;
      if (!reportId) {
        const draft = await createReportDraft(reportInput(), session.access_token);
        reportId = draft.id;
        setDraftId(reportId);
      }
      const alert = await raiseAlert(reportId, location, session.access_token);
      setUrgentAlert(alert);
      setStep("urgent");
    } catch {
      setAlertFailed(true);
    } finally {
      setAlerting(false);
    }
  }

  async function submit() {
    if (!validateRequiredFields()) {
      setFailed(false);
      return;
    }
    setSubmitting(true);
    setFailed(false);

    try {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) {
        throw new Error("session_required");
      }

      const report = await fileReport(
        reportInput(),
        session.access_token,
        photo
          ? {
              client: supabase,
              file: photo,
              userId: session.user.id,
              caption: description.trim(),
          }
          : undefined,
        draftId ?? undefined,
        transcriptId ?? undefined,
        audioMediaId ?? undefined,
      );
      try {
        sessionStorage.setItem(
          `safeloop-report-${report.id}`,
          report.human_ref,
        );
      } catch {
        // The confirmation page falls back to the report ID when storage is blocked.
      }
      router.push(`/${locale}/report/${report.id}`);
    } catch {
      setFailed(true);
      setSubmitting(false);
    }
  }

  if (step === "urgent" && urgentAlert) {
    const copyKey = reporterAlertCopyKey(urgentAlert);
    const acknowledged = copyKey === "alert.reporter.acknowledged";
    const escalated = copyKey === "alert.reporter.escalated";
    const screenStyle = acknowledged
      ? "from-success to-successStrong"
      : escalated
        ? "from-warning to-dangerStrong"
        : "from-danger to-dangerStrong";
    return (
      <main className={`mx-auto flex min-h-screen max-w-[430px] flex-col bg-gradient-to-br ${screenStyle} px-6 py-10 text-ink-inverse`}>
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <div className="grid h-24 w-24 place-items-center rounded-full border-4 border-ink-inverse/40 bg-ink-inverse/15">
            {acknowledged ? (
              <CheckCircleIcon className="h-14 w-14" />
            ) : (
              <ExclamationTriangleIcon className="h-14 w-14" />
            )}
          </div>
          <h1 className="mt-7 text-3xl font-bold">
            {t(`${copyKey}.title`, {
              name: urgentAlert.acknowledged_by_name ?? "",
            })}
          </h1>
          <p className="mt-4 text-lg">
            {t(`${copyKey}.detail`, {
              name: urgentAlert.acknowledged_by_name ?? "",
              location: urgentAlert.location_text ?? t("alert.locationUnknown"),
            })}
          </p>
          <div className="mt-6 flex items-center gap-2 rounded-chip bg-ink-inverse/15 px-4 py-3 text-base font-bold">
            <ClockIcon className="h-5 w-5" />
            <span>{t(`${copyKey}.status`, {
              sentTime: formatDateTime(urgentAlert.raised_at, locale),
              acknowledgedTime: urgentAlert.acknowledged_at
                ? formatDateTime(urgentAlert.acknowledged_at, locale)
                : "",
              name: urgentAlert.acknowledged_by_name ?? "",
            })}</span>
          </div>
        </div>
        <div className="space-y-4 pt-8 text-center">
          <SecondaryButton
            className="border-ink-inverse bg-ink-inverse text-dangerStrong"
            label={t("alert.reporter.continue")}
            onClick={() => setStep("review")}
          />
          <p className="text-base font-bold">
            {t("alert.reporter.emergency", { number: siteEmergencyLine })}
          </p>
        </div>
      </main>
    );
  }

  const stepNumber = step === "capture" ? 1 : step === "question" ? 2 : 3;
  const title =
    step === "capture"
      ? t(captureMode === "voice"
          ? "report.new.captureTitle"
          : "report.new.typeTitle")
      : step === "question"
        ? t("report.new.questionTitle")
        : t("report.new.reviewTitle");
  const missingFieldLabels = missingFields.map((field) =>
    t(`report.new.validation.field.${field}`),
  );

  return (
    <div className="mx-auto flex min-h-screen max-w-[430px] flex-col bg-bg text-ink">
      <main className="flex-1 px-5 pb-6">
      <header className="grid grid-cols-[44px_1fr_64px] items-center py-5">
        <button
          type="button"
          className="grid min-h-11 min-w-11 place-items-center rounded-control border border-border bg-surface"
          aria-label={t(step === "capture" && captureMode === "voice"
            ? "report.new.close"
            : "report.new.back")}
          onClick={() => {
            if (step === "capture" && captureMode === "voice") router.back();
            else if (step === "capture") setCaptureMode("voice");
            else setStep(step === "review" ? "question" : "capture");
          }}
        >
          {step === "capture" && captureMode === "voice"
            ? <XMarkIcon className="h-7 w-7" />
            : <ArrowLeftIcon className="h-7 w-7" />}
        </button>
        <h1 className="text-center text-xl font-bold">{title}</h1>
        {step === "capture" ? <span /> : (
          <span className="whitespace-nowrap text-right text-base font-bold">
            {t("report.new.step", { current: stepNumber, total: 3 })}
          </span>
        )}
      </header>

      {step === "capture" && (
        <div className={captureMode === "voice"
          ? "flex min-h-[calc(100dvh-164px)] flex-col pb-2"
          : "space-y-5 pb-4"}
        >
          {captureMode === "voice" ? (
            <div className="mb-6 text-center">
              <h2 className="text-2xl font-bold">{t("report.new.voiceFirstTitle")}</h2>
              <p className="mx-auto mt-2 max-w-sm text-base leading-6 text-inkMuted">
                {t("report.new.voiceFirstDetail")}
              </p>
            </div>
          ) : (
            <button
              type="button"
              className="flex min-h-14 w-full items-center justify-center gap-3 rounded-card bg-surfaceSunken px-4 text-base font-bold text-primaryStrong"
              onClick={() => setCaptureMode("voice")}
            >
              <MicrophoneIcon className="h-6 w-6" />
              <span>{t("report.new.preferVoice")}</span>
            </button>
          )}

          <VoiceConfirmedTextarea
            id="capture-description"
            rows={captureMode === "typed" ? 7 : 5}
            presentation={captureMode}
            onTranscriptionSettled={() => setCaptureMode("typed")}
            label={t("report.new.requiredLabel", {
              label: t("report.new.whatHappened"),
            })}
            placeholder={t("report.new.descriptionExample", {
              guardrail: t("term.guardrail"),
            })}
            required
            error={missingFields.includes("description")
              ? t("report.new.validation.description")
              : undefined}
            value={description}
            locale={locale}
            reportId={draftId ?? undefined}
            ensureReportId={ensureVoiceDraft}
            onTranscriptIdChange={setTranscriptId}
            onMediaIdChange={setAudioMediaId}
            onProcessingChange={setVoiceProcessing}
            onChange={(value) => {
              setDescription(value);
              clearMissingField("description", value);
            }}
          />

          {captureMode === "voice" ? (
            <>
              <div
                className="mx-auto mt-7 flex rounded-chip bg-surfaceSunken p-1"
                aria-label={t("app.language")}
              >
                {[locales[1], locales[0]].map((item) => (
                  <button
                    type="button"
                    key={item}
                    aria-pressed={item === langOriginal}
                    onClick={() => switchReportLanguage(item)}
                    className={`min-h-11 min-w-[104px] rounded-chip px-4 text-base font-bold ${
                      item === langOriginal
                        ? "bg-surface text-ink shadow-safe"
                        : "text-inkMuted"
                    }`}
                  >
                    {item === locales[0]
                      ? t("app.languageEnglish")
                      : t("report.new.languageChineseShort")}
                  </button>
                ))}
              </div>

              <div className="mt-auto grid grid-cols-2 gap-3 pt-8">
                <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-card border border-border bg-surface px-3 text-center shadow-safe">
                  {photoUrl ? (
                    <Image
                      src={photoUrl}
                      alt={t("report.new.changePhoto")}
                      width={240}
                      height={160}
                      unoptimized
                      className="mb-2 h-12 w-16 rounded-tile object-cover"
                    />
                  ) : (
                    <CameraIcon className="mb-2 h-8 w-8 text-primaryStrong" />
                  )}
                  <strong>{photo ? t("report.new.changePhoto") : t("report.new.addPhoto")}</strong>
                  <span className="mt-1 text-sm text-inkMuted">{t("report.new.optional")}</span>
                  <input
                    className="sr-only"
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    capture="environment"
                    onChange={selectPhoto}
                  />
                </label>
                <button
                  type="button"
                  className="flex min-h-28 flex-col items-center justify-center rounded-card border border-border bg-surface px-3 text-center shadow-safe"
                  onClick={() => setCaptureMode("typed")}
                >
                  <PencilSquareIcon className="mb-2 h-8 w-8 text-primaryStrong" />
                  <strong>{t("report.new.typeInstead")}</strong>
                  <span className="mt-1 text-sm text-inkMuted">{t("report.new.typeInsteadDetail")}</span>
                </button>
              </div>
            </>
          ) : (
            <>
              <div>
                <p className="mb-2 text-sm font-bold uppercase tracking-wide">
                  {t("report.new.addPhoto")}
                </p>
                <label className="flex min-h-24 cursor-pointer items-center gap-3 rounded-card border border-dashed border-border bg-surface px-5 text-inkMuted">
                  {photoUrl ? (
                    <Image
                      src={photoUrl}
                      alt={t("report.new.changePhoto")}
                      width={240}
                      height={160}
                      unoptimized
                      className="h-14 w-20 rounded-tile object-cover"
                    />
                  ) : (
                    <CameraIcon className="h-7 w-7 shrink-0 text-primaryStrong" />
                  )}
                  <span>{photo ? t("report.new.changePhoto") : t("report.new.photoSafeLong")}</span>
                  <input
                    className="sr-only"
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    capture="environment"
                    onChange={selectPhoto}
                  />
                </label>
              </div>
              <Field
                id="capture-location"
                label={t("report.new.requiredLabel", {
                  label: t("report.new.location"),
                })}
                placeholder={t("report.new.locationPlaceholder")}
                required
                error={missingFields.includes("location")
                  ? t("report.new.validation.location")
                  : undefined}
                value={location}
                onChange={(event) => {
                  setLocation(event.target.value);
                  clearMissingField("location", event.target.value);
                }}
              />
              {missingFields.length > 0 && (
                <div role="alert">
                  <Banner
                    tone="warning"
                    title={t("report.new.validation.title")}
                    detail={t("report.new.validation.detail", {
                      fields: missingFieldLabels.join(", "),
                    })}
                  />
                </div>
              )}
              <PrimaryButton
                label={t("report.new.continue")}
                disabled={voiceProcessing}
                onClick={continueFromCapture}
              />
            </>
          )}
        </div>
      )}

      {step === "question" && (
        <div className="space-y-5">
          <div className="flex justify-center gap-2" aria-hidden="true">
            <span className="h-2.5 w-8 rounded-chip bg-primary" />
          </div>
          <p className="text-center text-base text-inkMuted">
            {t("report.new.questionCount", { count: 1 })}
          </p>
          <Card className="flex min-h-[500px] flex-col justify-center gap-5 py-10 text-center">
            <ShieldExclamationIcon className="mx-auto h-20 w-20 text-danger" />
            <h2 className="text-2xl font-bold">
              {t("report.new.dangerQuestion")}
            </h2>
            {(["yes", "no"] as const).map((answer) => (
              <button
                key={answer}
                type="button"
                className={`flex min-h-16 w-full items-center gap-3 rounded-control border px-4 text-left text-base font-bold focus:outline-none focus:ring-2 focus:ring-primaryStrong ${
                  danger === answer
                    ? "border-primary bg-primaryTint"
                    : "border-border bg-surface"
                }`}
                onClick={() =>
                  answer === "yes" ? void sendUrgentAlert() : setDanger("no")
                }
                disabled={alerting}
              >
                {answer === "yes" ? (
                  <ExclamationTriangleIcon className="h-7 w-7 shrink-0 text-danger" />
                ) : danger === answer ? (
                  <CheckCircleIcon className="h-7 w-7 shrink-0 text-primaryStrong" />
                ) : (
                  <span className="h-7 w-7 shrink-0" aria-hidden="true" />
                )}
                <span>
                  {t(
                    answer === "yes"
                      ? "report.new.dangerYes"
                      : "report.new.dangerNo",
                  )}
                </span>
              </button>
            ))}
          </Card>
          {alertFailed && (
            <Banner
              tone="urgent"
              title={t("alert.reporter.failedTitle")}
              detail={t("alert.reporter.failedDetail", { number: siteEmergencyLine })}
            />
          )}
          <PrimaryButton
            label={alerting ? t("alert.reporter.sending") : t("report.new.continue")}
            disabled={!danger || danger === "yes" || alerting}
            onClick={() => setStep("review")}
          />
        </div>
      )}

      {step === "review" && (
        <div className="space-y-4">
          {photoUrl && (
            <div className="flex gap-2.5">
              <Image
                className="h-32 min-w-0 flex-[5] rounded-tile object-cover"
                src={photoUrl}
                alt={t("report.new.changePhoto")}
                width={320}
                height={256}
                unoptimized
              />
              <button
                type="button"
                className="grid h-32 flex-[4] place-items-center rounded-tile border border-dashed border-border bg-surfaceSunken"
                onClick={() => setStep("capture")}
                aria-label={t("report.new.addPhoto")}
              >
                <PlusIcon className="h-8 w-8" />
              </button>
            </div>
          )}
          <Card className="space-y-4">
            <h2 className="text-2xl font-bold">
              {t("report.new.whatReported")}
            </h2>
            <VoiceConfirmedTextarea
              id="report-description"
              rows={4}
              label={t("report.new.requiredLabel", {
                label: t("report.new.whatHappened"),
              })}
              required
              error={missingFields.includes("description")
                ? t("report.new.validation.description")
                : undefined}
              value={description}
              locale={locale}
              reportId={draftId ?? undefined}
              ensureReportId={ensureVoiceDraft}
              onTranscriptIdChange={setTranscriptId}
              onMediaIdChange={setAudioMediaId}
              onProcessingChange={setVoiceProcessing}
              onChange={(value) => {
                setDescription(value);
                clearMissingField("description", value);
              }}
            />
            <Field
              id="report-location"
              label={t("report.new.requiredLabel", {
                label: t("report.new.location"),
              })}
              placeholder={t("report.new.locationPlaceholder")}
              required
              error={missingFields.includes("location")
                ? t("report.new.validation.location")
                : undefined}
              value={location}
              onChange={(event) => {
                setLocation(event.target.value);
                clearMissingField("location", event.target.value);
              }}
            />
            <Field
              id="report-activity"
              label={t("report.new.requiredLabel", {
                label: t("report.new.activity"),
              })}
              placeholder={t("report.new.activityPlaceholder")}
              required
              error={missingFields.includes("activity")
                ? t("report.new.validation.activity")
                : undefined}
              value={activity}
              onChange={(event) => {
                setActivity(event.target.value);
                clearMissingField("activity", event.target.value);
              }}
            />
            <button
              type="button"
              className="flex min-h-11 w-full items-center justify-between text-left text-base font-bold"
              onClick={() => setDetailsOpen((open) => !open)}
            >
              <span>
                {t("report.new.moreDetail")} {" "}
                <span className="font-normal text-inkMuted">
                  {t("report.new.optional")}
                </span>
              </span>
              <ChevronDownIcon
                className={`h-5 w-5 transition ${
                  detailsOpen ? "rotate-180" : ""
                }`}
              />
            </button>
            {detailsOpen && (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label={t("report.new.levelOrZone")}
                  value={levelOrZone}
                  onChange={(event) => setLevelOrZone(event.target.value)}
                />
                <Field
                  label={t("report.new.gridRef")}
                  value={gridRef}
                  onChange={(event) => setGridRef(event.target.value)}
                />
              </div>
            )}
            <label className="flex min-h-11 items-center justify-between gap-4 text-base font-bold">
              <span>{t("report.new.confidential")}</span>
              <input
                type="checkbox"
                className="h-6 w-6 accent-primary"
                checked={confidential}
                onChange={(event) => setConfidential(event.target.checked)}
              />
            </label>
          </Card>
          {failed && (
            <Banner
              tone="warning"
              title={t("report.new.failureTitle")}
              detail={t("report.new.failureDetail")}
            />
          )}
          {missingFields.length > 0 && (
            <div role="alert">
              <Banner
                tone="warning"
                title={t("report.new.validation.title")}
                detail={t("report.new.validation.detail", {
                  fields: missingFieldLabels.join(", "),
                })}
              />
            </div>
          )}
          <PrimaryButton
            label={
              submitting
                ? t("report.new.submitting")
                : t("report.new.submit")
            }
            disabled={submitting || voiceProcessing}
            onClick={() => void submit()}
          />
          {failed && (
            <SecondaryButton
              label={t("report.new.retry")}
              onClick={() => void submit()}
            />
          )}
        </div>
      )}
      </main>
      <BottomNavigation
        items={navItems}
        activeHref={`/${locale}/report/new`}
      />
    </div>
  );
}
