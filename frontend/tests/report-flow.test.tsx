import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReportFlow } from "../components/reports/ReportFlow";
import en from "../messages/en.json";
import zh from "../messages/zh-CN.json";
import { getAlert, raiseAlert } from "../lib/alerts";
import { defaultLocale, locales } from "../lib/locales";
import { createReportDraft, fileReport } from "../lib/reports";
import { uploadReportAudio } from "../lib/media";
import { transcribeAudio } from "../lib/transcription";
import { reportStatus } from "../lib/stateMachine";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
}));
const supabase = vi.hoisted(() => ({
  auth: {
    getSession: async () => ({
      data: {
        session: {
          access_token: "test-token",
          user: { id: "reporter-id" },
        },
      },
    }),
  },
  storage: {},
}));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("../lib/reports", () => ({
  createReportDraft: vi.fn(),
  fileReport: vi.fn(),
}));
vi.mock("../lib/media", () => ({
  mediaPhase: { original: "original", evidence: "evidence" },
  uploadReportAudio: vi.fn(),
}));
vi.mock("../lib/transcription", () => ({
  transcribeAudio: vi.fn(),
}));
vi.mock("../components/reports/VoiceRecorder", () => ({
  VoiceRecorder: ({ onChange }: { onChange: (file: File | null) => void }) => (
    <button
      type="button"
      aria-label="mock voice recording"
      onClick={() => onChange(new File(["voice"], "report.webm", { type: "audio/webm" }))}
    >
      Record
    </button>
  ),
}));
vi.mock("../lib/alerts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/alerts")>();
  return { ...actual, getAlert: vi.fn(), raiseAlert: vi.fn() };
});
vi.mock("../lib/supabase/browser", () => ({ createClient: () => supabase }));

function expand(flat: Record<string, string>): AbstractIntlMessages {
  const result: AbstractIntlMessages = {};
  for (const [key, value] of Object.entries(flat)) {
    const parts = key.split(".");
    let cursor: AbstractIntlMessages = result;
    for (const part of parts.slice(0, -1)) cursor = (cursor[part] ??= {}) as AbstractIntlMessages;
    cursor[parts.at(-1)!] = value;
  }
  return result;
}

function renderFlow(locale = defaultLocale) {
  const flat = locale === defaultLocale ? en : zh;
  return render(<NextIntlClientProvider locale={locale} messages={expand(flat)}><ReportFlow /></NextIntlClientProvider>);
}

function requiredLabel(label: string): string {
  return en["report.new.requiredLabel"].replace("{label}", label);
}

async function reachReview(description: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Type instead/ }));
  await user.type(screen.getByLabelText(requiredLabel(en["report.new.whatHappened"])), description);
  await user.type(screen.getByLabelText(requiredLabel(en["report.new.location"])), "Level 6");
  await user.click(screen.getByRole("button", { name: en["report.new.continue"] }));
  await user.click(screen.getByRole("button", { name: en["report.new.dangerNo"] }));
  await user.click(screen.getByRole("button", { name: en["report.new.continue"] }));
  await user.type(screen.getByLabelText(requiredLabel(en["report.new.activity"])), "Material delivery");
  return user;
}

const sentAlert = {
  id: "alert-id",
  report_id: "draft-id",
  human_ref: "SL-2026-00001",
  description_original: "Loose edge protection",
  raised_by: "reporter-id",
  raised_at: "2026-08-22T08:00:00Z",
  location_text: "Level 6",
  acknowledged_by: null,
  acknowledged_by_name: null,
  acknowledged_at: null,
  escalated_at: null,
  resolution_note: null,
};

describe("ReportFlow", () => {
  beforeEach(() => {
    navigation.push.mockReset();
    navigation.replace.mockReset();
    navigation.refresh.mockReset();
    vi.mocked(createReportDraft).mockReset();
    vi.mocked(fileReport).mockReset();
    vi.mocked(uploadReportAudio).mockReset();
    vi.mocked(transcribeAudio).mockReset();
    vi.mocked(getAlert).mockReset();
    vi.mocked(raiseAlert).mockReset();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:test-photo"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });
  afterEach(cleanup);

  it("creates a draft, submits it, and redirects", async () => {
    vi.mocked(fileReport).mockResolvedValue({ id: "report-id", human_ref: "SL-2026-00001", status: reportStatus.submitted });
    renderFlow();
    expect(screen.getByRole("heading", { name: en["report.new.voiceFirstTitle"] })).toBeTruthy();
    expect(screen.queryByRole("link", { name: en["app.myReports"] })).toBeNull();
    const user = await reachReview("Loose edge protection");
    await user.click(screen.getByRole("button", { name: en["report.new.submit"] }));
    await waitFor(() => expect(navigation.push).toHaveBeenCalledWith(`/${defaultLocale}/report/report-id`));
    expect(fileReport).toHaveBeenCalledWith(
      expect.objectContaining({
        description_original: "Loose edge protection",
      }),
      "test-token",
      undefined,
      undefined,
      undefined,
    );
  });

  it("names missing required fields when Continue is clicked", async () => {
    renderFlow();
    await userEvent.click(screen.getByRole("button", { name: /Type instead/ }));
    await userEvent.click(screen.getByRole("button", { name: en["report.new.continue"] }));

    expect(screen.getByRole("alert").textContent).toContain(en["report.new.validation.title"]);
    expect(screen.getByRole("alert").textContent).toContain("What happened, Location");
    expect(screen.getByText(en["report.new.validation.description"])).toBeTruthy();
    expect(screen.getByText(en["report.new.validation.location"])).toBeTruthy();
    expect(screen.getByRole("heading", { name: en["report.new.typeTitle"] })).toBeTruthy();
  });

  it("starts voice-first and lets the reporter switch to the complete typed form", async () => {
    renderFlow();
    const user = userEvent.setup();

    expect(screen.getByRole("heading", { name: en["report.new.voiceFirstTitle"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: "mock voice recording" })).toBeTruthy();
    expect(screen.queryByLabelText(requiredLabel(en["report.new.whatHappened"]))).toBeNull();

    await user.click(screen.getByRole("button", { name: /Type instead/ }));

    expect(screen.getByRole("heading", { name: en["report.new.typeTitle"] })).toBeTruthy();
    expect(screen.getByLabelText(requiredLabel(en["report.new.whatHappened"]))).toBeTruthy();
    expect(screen.getByLabelText(requiredLabel(en["report.new.location"]))).toBeTruthy();
    expect(screen.getByRole("button", { name: en["report.new.continue"] })).toBeTruthy();
  });

  it("switches the report page and remembered locale with report language", async () => {
    const view = renderFlow();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Type instead/ }));
    await user.type(
      screen.getByLabelText(requiredLabel(en["report.new.whatHappened"])),
      "Keep this description",
    );
    await user.type(
      screen.getByLabelText(requiredLabel(en["report.new.location"])),
      "Level 8",
    );

    await user.click(screen.getByRole("button", { name: en["report.new.preferVoice"] }));
    await user.click(screen.getByRole("button", { name: en["report.new.languageChineseShort"] }));

    expect(document.cookie).toContain("safeloop-locale=zh-CN");
    expect(navigation.replace).toHaveBeenCalledWith("/zh-CN/report/new");
    expect(navigation.refresh).toHaveBeenCalledOnce();

    view.unmount();
    renderFlow(locales[1]);
    await user.click(screen.getByRole("button", { name: new RegExp(zh["report.new.typeInstead"]) }));
    expect(
      (screen.getByLabelText(
        zh["report.new.requiredLabel"].replace(
          "{label}",
          zh["report.new.whatHappened"],
        ),
      ) as HTMLTextAreaElement).value,
    ).toBe("Keep this description");
    expect(
      (screen.getByLabelText(
        zh["report.new.requiredLabel"].replace(
          "{label}",
          zh["report.new.location"],
        ),
      ) as HTMLInputElement).value,
    ).toBe("Level 8");
  });

  it("passes the selected photo and authenticated storage client to submission", async () => {
    vi.mocked(fileReport).mockResolvedValue({ id: "report-id", human_ref: "SL-2026-00001", status: reportStatus.submitted });
    renderFlow();
    const file = new File(["photo"], "hazard.jpg", { type: "image/jpeg" });
    const inputElement = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(inputElement).not.toBeNull();
    await userEvent.upload(inputElement!, file);
    const user = await reachReview("Loose edge protection");
    await user.click(screen.getByRole("button", { name: en["report.new.submit"] }));
    await waitFor(() => expect(navigation.push).toHaveBeenCalledWith(`/${defaultLocale}/report/report-id`));
    expect(fileReport).toHaveBeenCalledWith(
      expect.any(Object),
      "test-token",
      expect.objectContaining({
        client: supabase,
        file,
        userId: "reporter-id",
        caption: "Loose edge protection",
      }),
      undefined,
      undefined,
    );
  });

  it("keeps typed input when submission fails", async () => {
    vi.mocked(fileReport).mockRejectedValue(new Error("offline"));
    renderFlow();
    const user = await reachReview("Keep this text");
    await user.click(screen.getByRole("button", { name: en["report.new.submit"] }));
    await screen.findByText(en["report.new.failureTitle"]);
    expect((screen.getByLabelText(requiredLabel(en["report.new.whatHappened"])) as HTMLTextAreaElement).value).toBe("Keep this text");
  });

  it("puts a Mandarin transcript into the editable field and submits the correction", async () => {
    vi.mocked(createReportDraft).mockResolvedValue({ id: "draft-id" });
    vi.mocked(uploadReportAudio).mockResolvedValue({ id: "audio-id" } as never);
    vi.mocked(transcribeAudio).mockResolvedValue({
      transcript_id: "transcript-id",
      text: "六楼边缘没有护栏",
      detected_locale: "zh-CN",
      confidence: 0.94,
      duration_ms: 30000,
      provider: "stub",
      model: "stub-v1",
      provider_ref: "stub-ref",
      latency_ms: 1,
      meets_confidence_threshold: true,
    });
    vi.mocked(fileReport).mockResolvedValue({
      id: "draft-id",
      human_ref: "SL-2026-00001",
      status: reportStatus.submitted,
    });
    renderFlow();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "mock voice recording" }));
    const description = screen.getByLabelText(
      requiredLabel(en["report.new.whatHappened"]),
    ) as HTMLTextAreaElement;
    await waitFor(() => expect(description.value).toBe("六楼边缘没有护栏"));
    expect(document.activeElement).toBe(description);
    const transcriptBanner = screen
      .getByText(en["report.voice.transcription.ready.title"])
      .closest("aside");
    expect(transcriptBanner?.textContent).toContain("zh-CN");
    expect(transcriptBanner?.textContent).toContain("check it");

    await user.clear(description);
    await user.type(description, "六楼边缘没有防护栏");
    await user.type(
      screen.getByLabelText(requiredLabel(en["report.new.location"])),
      "Level 6",
    );
    await user.click(screen.getByRole("button", { name: en["report.new.continue"] }));
    await user.click(screen.getByRole("button", { name: en["report.new.dangerNo"] }));
    await user.click(screen.getByRole("button", { name: en["report.new.continue"] }));
    await user.type(
      screen.getByLabelText(requiredLabel(en["report.new.activity"])),
      "Material delivery",
    );
    await user.click(screen.getByRole("button", { name: en["report.new.submit"] }));

    await waitFor(() => expect(fileReport).toHaveBeenCalledWith(
      expect.objectContaining({ description_original: "六楼边缘没有防护栏" }),
      "test-token",
      undefined,
      "draft-id",
      "transcript-id",
    ));
  });

  it("keeps filing available with typed text after transcription fails", async () => {
    vi.mocked(createReportDraft).mockResolvedValue({ id: "draft-id" });
    vi.mocked(uploadReportAudio).mockResolvedValue({ id: "audio-id" } as never);
    vi.mocked(transcribeAudio).mockRejectedValue(new Error("provider unavailable"));
    vi.mocked(fileReport).mockResolvedValue({
      id: "draft-id",
      human_ref: "SL-2026-00001",
      status: reportStatus.submitted,
    });
    renderFlow();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "mock voice recording" }));
    await screen.findByText(en["report.voice.transcription.failed.title"]);
    const description = screen.getByLabelText(
      requiredLabel(en["report.new.whatHappened"]),
    ) as HTMLTextAreaElement;
    expect(description.value).toBe("");
    expect(document.activeElement).toBe(description);

    await user.type(description, "Typed fallback after voice failed");
    await user.type(
      screen.getByLabelText(requiredLabel(en["report.new.location"])),
      "Level 6",
    );
    await user.click(screen.getByRole("button", { name: en["report.new.continue"] }));
    await user.click(screen.getByRole("button", { name: en["report.new.dangerNo"] }));
    await user.click(screen.getByRole("button", { name: en["report.new.continue"] }));
    await user.type(
      screen.getByLabelText(requiredLabel(en["report.new.activity"])),
      "Material delivery",
    );
    await user.click(screen.getByRole("button", { name: en["report.new.submit"] }));

    await waitFor(() => expect(fileReport).toHaveBeenCalledWith(
      expect.objectContaining({
        description_original: "Typed fallback after voice failed",
      }),
      "test-token",
      undefined,
      "draft-id",
      undefined,
    ));
  });

  it("leaves the editable field ready for typing after a low-confidence transcript", async () => {
    vi.mocked(createReportDraft).mockResolvedValue({ id: "draft-id" });
    vi.mocked(uploadReportAudio).mockResolvedValue({ id: "audio-id" } as never);
    vi.mocked(transcribeAudio).mockResolvedValue({
      transcript_id: "transcript-id",
      text: "uncertain partial text",
      detected_locale: "mul",
      confidence: 0.3,
      duration_ms: 30000,
      provider: "stub",
      model: "stub-v1",
      provider_ref: "stub-ref",
      latency_ms: 1,
      meets_confidence_threshold: false,
    });
    renderFlow();

    await userEvent.click(
      screen.getByRole("button", { name: "mock voice recording" }),
    );
    await screen.findByText(en["report.voice.transcription.lowConfidence.title"]);
    const description = screen.getByLabelText(
      requiredLabel(en["report.new.whatHappened"]),
    ) as HTMLTextAreaElement;
    expect(description.value).toBe("");
    expect(document.activeElement).toBe(description);
    expect(
      screen.getByText(en["report.voice.transcription.lowConfidence.title"])
        .closest("aside")?.textContent,
    ).toContain("mul");
  });

  it("names every missing required field before submitting", async () => {
    renderFlow();
    const user = await reachReview("Loose edge protection");
    await user.clear(screen.getByLabelText(requiredLabel(en["report.new.whatHappened"])));
    await user.clear(screen.getByLabelText(requiredLabel(en["report.new.location"])));
    await user.clear(screen.getByLabelText(requiredLabel(en["report.new.activity"])));

    await user.click(screen.getByRole("button", { name: en["report.new.submit"] }));

    expect(screen.getByRole("alert").textContent).toContain(en["report.new.validation.title"]);
    expect(screen.getByRole("alert").textContent).toContain("What happened, Location, Activity");
    expect(screen.getByText(en["report.new.validation.description"])).toBeTruthy();
    expect(screen.getByText(en["report.new.validation.location"])).toBeTruthy();
    expect(screen.getByText(en["report.new.validation.activity"])).toBeTruthy();
    expect(fileReport).not.toHaveBeenCalled();
  });

  it("raises an alert on the draft before submission and says sent, not seen", async () => {
    vi.mocked(createReportDraft).mockResolvedValue({ id: "draft-id" });
    vi.mocked(raiseAlert).mockResolvedValue(sentAlert);
    renderFlow();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Type instead/ }));
    await user.type(
      screen.getByLabelText(requiredLabel(en["report.new.whatHappened"])),
      "Loose edge protection",
    );
    await user.type(screen.getByLabelText(requiredLabel(en["report.new.location"])), "Level 6");
    await user.click(screen.getByRole("button", { name: en["report.new.continue"] }));
    await user.click(screen.getByRole("button", { name: en["report.new.dangerYes"] }));

    expect(await screen.findByRole("heading", { name: en["alert.reporter.sent.title"] })).toBeTruthy();
    expect(screen.queryByText(/has seen/i)).toBeNull();
    expect(createReportDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        description_original: "Loose edge protection",
        location_text: "Level 6",
      }),
      "test-token",
    );
    expect(raiseAlert).toHaveBeenCalledWith("draft-id", "Level 6", "test-token");
    expect(fileReport).not.toHaveBeenCalled();
  });

  it.each([[locales[0], en["report.new.captureTitle"]], [locales[1], zh["report.new.captureTitle"]]])("renders the capture page in %s", (locale, title) => {
    renderFlow(locale);
    expect(screen.getByRole("heading", { name: title })).toBeTruthy();
  });
});
