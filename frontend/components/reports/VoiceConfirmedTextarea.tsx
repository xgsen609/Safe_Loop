"use client";

import { useRef, useState, type TextareaHTMLAttributes } from "react";
import { useTranslations } from "next-intl";

import type { Locale } from "../../lib/locales";
import { mediaPhase, uploadReportAudio, type MediaPhase } from "../../lib/media";
import { transcribeAudio } from "../../lib/transcription";
import {
  commitLiveTranscript,
  startLiveTranscription,
  type LiveTranscriptDraft,
} from "../../lib/live-transcription";
import { createClient } from "../../lib/supabase/browser";
import { Banner } from "../ui/Banner";
import { Field } from "../ui/Field";
import { VoiceRecorder } from "./VoiceRecorder";

type State = "processing" | "ready" | "lowConfidence" | "failed" | null;

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "onChange"> & {
  label: string;
  error?: string;
  value: string;
  onChange: (value: string) => void;
  locale: Locale;
  reportId?: string;
  ensureReportId?: () => Promise<string>;
  phase?: MediaPhase;
  onTranscriptIdChange: (transcriptId: string | null) => void;
  onMediaIdChange?: (mediaId: string | null) => void;
  onProcessingChange?: (processing: boolean) => void;
  presentation?: "voice" | "typed" | "inline";
  onTranscriptionSettled?: () => void;
};

export function VoiceConfirmedTextarea({
  label,
  value,
  onChange,
  locale,
  reportId,
  ensureReportId,
  phase = mediaPhase.original,
  onTranscriptIdChange,
  onMediaIdChange,
  onProcessingChange,
  presentation = "inline",
  onTranscriptionSettled,
  rows = 4,
  ...fieldProps
}: Props) {
  const t = useTranslations();
  const [audio, setAudio] = useState<File | null>(null);
  const [state, setState] = useState<State>(null);
  const [detectedLocale, setDetectedLocale] = useState<string | null>(null);
  const [interimText, setInterimText] = useState("");
  const runRef = useRef(0);

  async function handleRecording(
    file: File | null,
    liveResult?: Promise<LiveTranscriptDraft | null>,
  ) {
    const run = runRef.current + 1;
    runRef.current = run;
    setAudio(file);
    onTranscriptIdChange(null);
    onMediaIdChange?.(null);
    setDetectedLocale(null);
    setInterimText("");
    setState(file ? "processing" : null);
    onProcessingChange?.(Boolean(file));
    if (!file) return;
    try {
      const client = createClient();
      const {
        data: { session },
      } = await client.auth.getSession();
      if (!session) throw new Error("session_required");
      const activeReportId = reportId ?? await ensureReportId?.();
      if (!activeReportId) throw new Error("report_required");
      const media = await uploadReportAudio({
        client,
        file,
        userId: session.user.id,
        reportId: activeReportId,
        accessToken: session.access_token,
        phase,
      });
      if (runRef.current !== run) return;
      onMediaIdChange?.(media.id);
      const live = await liveResult;
      let transcript;
      if (live) {
        try {
          transcript = await commitLiveTranscript(
            live.sessionId,
            media.id,
            session.access_token,
          );
        } catch {
          transcript = await transcribeAudio(
            media.id,
            locale === "zh-CN" ? "zh-CN" : "en-SG",
            session.access_token,
          );
        }
      } else {
        transcript = await transcribeAudio(
          media.id,
          locale === "zh-CN" ? "zh-CN" : "en-SG",
          session.access_token,
        );
      }
      if (runRef.current !== run) return;
      setDetectedLocale(transcript.detected_locale);
      if (!transcript.meets_confidence_threshold) {
        setState("lowConfidence");
        onTranscriptionSettled?.();
        return;
      }
      onChange(transcript.text);
      onTranscriptIdChange(transcript.transcript_id);
      setState("ready");
      onTranscriptionSettled?.();
    } catch {
      if (runRef.current !== run) return;
      setState("failed");
      onTranscriptionSettled?.();
    } finally {
      if (runRef.current === run) {
        onProcessingChange?.(false);
        window.setTimeout(() => {
          document.getElementById(String(fieldProps.id ?? ""))?.focus();
        }, 0);
      }
    }
  }

  return (
    <div className="space-y-3">
      {presentation !== "voice" && (
        <Field
          {...fieldProps}
          label={label}
          rows={rows}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {presentation !== "typed" && (
        <VoiceRecorder
          value={audio}
          onChange={(file, liveResult) => void handleRecording(file, liveResult)}
          variant={presentation === "voice" ? "hero" : "inline"}
          startLive={async (stream) => {
            const client = createClient();
            const { data: { session } } = await client.auth.getSession();
            if (!session) return null;
            return startLiveTranscription({
              stream,
              accessToken: session.access_token,
              hintLocale: locale === "zh-CN" ? "zh-CN" : "en-SG",
              onInterim: setInterimText,
            });
          }}
        />
      )}
      {interimText && (state === null || state === "processing") && (
        <div className="rounded-control border border-primary/30 bg-primaryTint px-4 py-3" aria-live="polite">
          <p className="text-xs font-bold uppercase tracking-wide text-inkMuted">
            {t("report.voice.liveInterim")}
          </p>
          <p className="mt-1 text-sm text-ink">{interimText}</p>
        </div>
      )}
      {state && state !== "processing" && (
        <Banner
          tone={state === "ready" ? "info" : "warning"}
          title={t(`report.voice.transcription.${state}.title`)}
          detail={t(`report.voice.transcription.${state}.detail`, {
            language: detectedLocale ?? t("report.voice.transcription.unknownLanguage"),
          })}
        />
      )}
      {state === "processing" && (
        <Banner
          tone="info"
          title={t("report.voice.transcription.processing.title")}
          detail={t("report.voice.transcription.processing.detail")}
        />
      )}
    </div>
  );
}
