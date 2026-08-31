import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReportDetail } from "../components/reports/ReportDetail";
import { defaultLocale, locales } from "../lib/locales";
import { mediaPhase } from "../lib/media";
import {
  answerClarification,
  getReport,
  getTimeline,
  transitionReport,
  type ReportDetail as ReportDetailData,
} from "../lib/reports";
import { reportStatus } from "../lib/stateMachine";
import en from "../messages/en.json";
import zh from "../messages/zh-CN.json";

vi.mock("../lib/reports", () => ({
  answerClarification: vi.fn(),
  getReport: vi.fn(),
  getTimeline: vi.fn(),
  transitionReport: vi.fn(),
}));
vi.mock("../components/reports/VoiceConfirmedTextarea", () => ({
  VoiceConfirmedTextarea: ({
    label,
    value,
    onChange,
    onTranscriptIdChange,
  }: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    onTranscriptIdChange: (id: string | null) => void;
  }) => (
    <>
      <label htmlFor="mock-clarification">{label}</label>
      <textarea id="mock-clarification" value={value} onChange={(event) => onChange(event.target.value)} />
      <button type="button" onClick={() => {
        onChange("语音回答已人工确认");
        onTranscriptIdChange("transcript-id");
      }}>mock voice clarification</button>
    </>
  ),
}));
vi.mock("../lib/supabase/browser", () => ({
  createClient: () => ({
    auth: {
      getSession: async () => ({
        data: { session: { access_token: "test-token" } },
      }),
    },
  }),
}));

function expand(flat: Record<string, string>): AbstractIntlMessages {
  const result: AbstractIntlMessages = {};
  for (const [key, value] of Object.entries(flat)) {
    const parts = key.split(".");
    let cursor: AbstractIntlMessages = result;
    for (const part of parts.slice(0, -1)) {
      cursor = (cursor[part] ??= {}) as AbstractIntlMessages;
    }
    cursor[parts.at(-1)!] = value;
  }
  return result;
}

function reportWith(
  availableTransitions: ReportDetailData["available_transitions"],
): ReportDetailData {
  return {
    id: "report-id",
    human_ref: "SL-2026-00001",
    status: reportStatus.under_review,
    urgency: "high",
    lang_original: locales[1],
    description_original: "六楼边缘没有护栏",
    description_en: "There is no guardrail at the Level 6 edge.",
    location_text: "Tower A",
    activity: "Material delivery",
    level_or_zone: "Level 6",
    grid_ref: "A4",
    submitted_at: "2026-08-22T01:02:00Z",
    closed_at: null,
    created_at: "2026-08-22T01:00:00Z",
    clarify_rounds: 0,
    clarifications: [],
    can_answer_clarifications: false,
    media: [
      {
        id: "media-id",
        storage_path: "private/photo.jpg",
        mime_type: "image/jpeg",
        phase: mediaPhase.original,
        caption: "Level 6 edge",
        signed_url: "https://project.example/photo.jpg?token=signed",
        signed_url_expires_at: "2026-08-22T01:10:00Z",
      },
    ],
    latest_draft: null,
    current_action: null,
    verifications: [],
    closure_receipt: null,
    available_transitions: availableTransitions,
  };
}

const timeline = [
  {
    id: "audit-id",
    event: "submit",
    actor_type: "human" as const,
    actor_role: "reporter" as const,
    actor_name: "Worker Tan",
    source: reportStatus.draft,
    target: reportStatus.submitted,
    reason: null,
    created_at: "2026-08-22T01:02:00Z",
  },
];

const closureTimeline = [
  timeline[0],
  {
    id: "review-audit-id",
    event: "approve_action",
    actor_type: "human" as const,
    actor_role: "reviewer" as const,
    source: reportStatus.under_review,
    target: reportStatus.action_assigned,
    reason: null,
    created_at: "2026-08-22T03:00:00Z",
  },
  {
    id: "close-audit-id",
    event: "verify_and_close",
    actor_type: "human" as const,
    actor_role: "reviewer" as const,
    source: reportStatus.action_submitted,
    target: reportStatus.verified_closed,
    reason: null,
    created_at: "2026-08-23T08:22:00Z",
  },
];

function closedReport(withPhotoPair = true): ReportDetailData {
  const report = reportWith([]);
  report.status = reportStatus.verified_closed;
  report.closed_at = "2026-08-23T08:22:00Z";
  report.current_action = {
    id: "action-id",
    assignment_id: "assignment-id",
    assignee_id: "responsible-id",
    assignee_name: "Ah Hock",
    assignment_active: true,
    action_text: "Installed and pull-tested both guardrail anchors.",
    status: "verified",
    rework_count: 0,
    due_at: "2026-08-24T09:00:00Z",
    completed_note: "Both anchors were replaced.",
    submitted_at: "2026-08-23T08:00:00Z",
  };
  report.verifications = [
    {
      id: "verification-id",
      corrective_action_id: "action-id",
      reviewer_id: "reviewer-id",
      reviewer_name: "SO Lim Wei Sheng",
      passed: true,
      checklist: { hazard_removed: true },
      notes: "Both anchors held during the final pull test.",
      reason: null,
      new_due_at: null,
      created_at: "2026-08-23T08:20:00Z",
    },
  ];
  if (withPhotoPair) {
    report.media.push({
      id: "after-media-id",
      storage_path: "private/evidence.jpg",
      mime_type: "image/jpeg",
      phase: mediaPhase.evidence,
      caption: null,
      corrective_action_id: "action-id",
      signed_url: "https://project.example/evidence.jpg?token=signed",
      signed_url_expires_at: "2026-08-23T08:32:00Z",
    });
  }
  report.closure_receipt = {
    id: "receipt-id",
    verification_id: "verification-id",
    corrective_action_id: "action-id",
    reporter_locale: locales[1],
    action_text: "Installed and pull-tested both guardrail anchors.",
    verification_notes: "Both anchors held during the final pull test.",
    verified_by_id: "reviewer-id",
    verified_by_name: "SO Lim Wei Sheng",
    before_media_id: withPhotoPair ? "media-id" : null,
    after_media_id: withPhotoPair ? "after-media-id" : null,
    created_at: "2026-08-23T08:22:00Z",
  };
  return report;
}

function renderDetail(locale = defaultLocale) {
  const messages = locale === defaultLocale ? en : zh;
  return render(
    <NextIntlClientProvider locale={locale} messages={expand(messages)}>
      <ReportDetail id="report-id" requestedLocale={locale} />
    </NextIntlClientProvider>,
  );
}

describe("ReportDetail", () => {
  beforeEach(() => {
    vi.mocked(answerClarification).mockReset();
    vi.mocked(getReport).mockReset();
    vi.mocked(getTimeline).mockReset();
    vi.mocked(transitionReport).mockReset();
    vi.mocked(getTimeline).mockResolvedValue(timeline);
    vi.mocked(transitionReport).mockResolvedValue({
      id: "report-id",
      status: reportStatus.rejected,
    });
    vi.mocked(answerClarification).mockResolvedValue({
      id: "clarification-id",
      report_id: "report-id",
      answered_at: "2026-08-22T01:05:00Z",
      round_complete: true,
    });
  });
  afterEach(cleanup);

  it("renders only server-returned reviewer actions and enforces a reason", async () => {
    vi.mocked(getReport).mockResolvedValue(
      reportWith([
        { event: "reject", target: reportStatus.rejected, requires_reason: true },
        {
          event: "approve_action",
          target: reportStatus.action_assigned,
          requires_reason: false,
        },
      ]),
    );
    renderDetail();

    expect(await screen.findByText("There is no guardrail at the Level 6 edge.")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Level 6 edge" })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["action.reject"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["action.approve_action"] })).toBeTruthy();
    expect(screen.queryByRole("button", { name: en["action.request_info"] })).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: en["action.reject"] }));
    const reason = screen.getByLabelText(en["report.detail.reason"]);
    const submit = screen.getByRole<HTMLButtonElement>("button", { name: en["action.reject"] });
    expect(submit.disabled).toBe(true);
    await user.type(reason, "The observation is not a site hazard.");
    expect(submit.disabled).toBe(false);
    await user.click(submit);

    await waitFor(() =>
      expect(transitionReport).toHaveBeenCalledWith(
        "report-id",
        reportStatus.rejected,
        "test-token",
        "The observation is not a site hazard.",
      ),
    );
  });

  it("plays stored audio from its signed URL without treating it as a photo", async () => {
    const report = reportWith([]);
    report.media.push({
      id: "audio-id",
      storage_path: "private/report.webm",
      mime_type: "audio/webm",
      phase: mediaPhase.original,
      caption: null,
      retention_until: "2026-11-20T01:00:00Z",
      signed_url: "https://project.example/report.webm?token=signed",
      signed_url_expires_at: "2026-08-22T01:10:00Z",
    });
    vi.mocked(getReport).mockResolvedValue(report);
    renderDetail();

    expect(await screen.findByText(en["report.media.audio"])).toBeTruthy();
    expect(document.querySelector("audio")?.getAttribute("src"))
      .toBe("https://project.example/report.webm?token=signed");
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });

  it("shows waiting copy when the server returns no reporter actions", async () => {
    vi.mocked(getReport).mockResolvedValue(reportWith([]));
    renderDetail();

    expect(await screen.findByText(en["workflow.next.under_review"])).toBeTruthy();
    expect(
      screen.getByText(
        en["workflow.next.title"].replace("{owner}", en["workflow.owner.reviewer"]),
      ),
    ).toBeTruthy();
    expect(screen.queryByText(en["report.detail.actions"])).toBeNull();
    expect(screen.queryByRole("button", { name: en["action.reject"] })).toBeNull();
    expect(screen.getByRole("link", { name: en["app.myReports"] }).getAttribute("href"))
      .toBe(`/${defaultLocale}/reports`);
    expect(screen.getByRole("link", { name: en["app.profile"] }).getAttribute("href"))
      .toBe(`/${defaultLocale}/profile`);
  });

  it("keeps reporter navigation available when the report cannot load", async () => {
    vi.mocked(getReport).mockRejectedValue(new Error("offline"));
    renderDetail();

    expect(await screen.findByText(en["report.detail.loadFailedTitle"])).toBeTruthy();
    expect(screen.getByRole("link", { name: en["app.myReports"] }).getAttribute("href"))
      .toBe(`/${defaultLocale}/reports`);
    expect(screen.getByRole("link", { name: en["app.inbox"] }).getAttribute("href"))
      .toBe(`/${defaultLocale}/inbox`);
  });

  it("lets the reporter answer a pending clarification without exposing an API code", async () => {
    const report = reportWith([]);
    report.status = reportStatus.clarifying;
    report.can_answer_clarifications = true;
    report.clarifications = [
      {
        id: "clarification-id",
        report_id: "report-id",
        round: 1,
        gap: "hazard_detail",
        question: "What exactly is unsafe?",
        answer: null,
        answered_at: null,
        created_at: "2026-08-22T01:04:00Z",
      },
    ];
    vi.mocked(getReport).mockResolvedValue(report);
    renderDetail();

    expect(await screen.findByText("What exactly is unsafe?")).toBeTruthy();
    const submit = screen.getByRole<HTMLButtonElement>("button", {
      name: en["report.clarification.submit"],
    });
    expect(submit.disabled).toBe(true);

    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText(en["report.clarification.answerLabel"]),
      "The temporary edge protection is missing.",
    );
    await user.click(submit);

    await waitFor(() =>
      expect(answerClarification).toHaveBeenCalledWith(
        "report-id",
        "clarification-id",
        "The temporary edge protection is missing.",
        "test-token",
      ),
    );
  });

  it("submits an editable voice clarification with transcript evidence", async () => {
    const report = reportWith([]);
    report.status = reportStatus.clarifying;
    report.can_answer_clarifications = true;
    report.clarifications = [{
      id: "clarification-id",
      report_id: "report-id",
      round: 1,
      gap: "hazard_detail",
      question: "What exactly is unsafe?",
      answer: null,
      answered_at: null,
      created_at: "2026-08-22T01:04:00Z",
    }];
    vi.mocked(getReport).mockResolvedValue(report);
    renderDetail();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "mock voice clarification" }));
    await user.click(screen.getByRole("button", { name: en["report.clarification.submit"] }));

    await waitFor(() => expect(answerClarification).toHaveBeenCalledWith(
      "report-id",
      "clarification-id",
      "语音回答已人工确认",
      "test-token",
      "transcript-id",
    ));
  });

  it("renders timeline verbs and actors in Simplified Chinese", async () => {
    vi.mocked(getReport).mockResolvedValue(reportWith([]));
    renderDetail(locales[1]);

    expect(await screen.findByText(zh["timeline.event.submit"])).toBeTruthy();
    expect(screen.getByText(new RegExp(zh["timeline.actor.reporter"]))).toBeTruthy();
    expect(screen.getByText(zh["report.detail.originalText"])).toBeTruthy();
  });

  it.each([
    [defaultLocale, en, "The team completed this work: Installed and pull-tested both guardrail anchors; Both anchors held during the final pull test; checked by SO Lim Wei Sheng."],
    [locales[1], zh, "团队完成了这项工作：Installed and pull-tested both guardrail anchors；检查记录：Both anchors held during the final pull test；由SO Lim Wei Sheng检查确认。"],
  ] as const)("renders the verified receipt and grouped timeline in %s", async (locale, messages, expectedSummary) => {
    vi.mocked(getReport).mockResolvedValue(closedReport());
    vi.mocked(getTimeline).mockResolvedValue(closureTimeline);
    renderDetail(locale);

    const receiptTitle = await screen.findByText(messages["receipt.title"]);
    expect(screen.getByTestId("closure-receipt-summary").textContent).toBe(expectedSummary);
    expect(screen.getByText(messages["receipt.timeline.reported"])).toBeTruthy();
    expect(screen.getByText(messages["receipt.timeline.reviewedAssigned"].replace("{assignee}", "Ah Hock"))).toBeTruthy();
    expect(screen.getByText(messages["receipt.timeline.fixedVerified"])).toBeTruthy();
    expect(screen.getByText(messages["receipt.timeline.closed"])).toBeTruthy();
    expect(screen.getByTestId("closure-receipt-photo-pair")).toBeTruthy();
    expect(screen.getByRole("img", { name: messages["receipt.beforeAlt"] })).toBeTruthy();
    expect(screen.getByRole("img", { name: messages["receipt.afterAlt"] })).toBeTruthy();
    const timelineTitle = screen.getByText(messages["report.detail.timeline"]);
    expect(timelineTitle.compareDocumentPosition(receiptTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("closure-receipt-summary").textContent).not.toContain("verified_closed");
  });

  it("omits the whole image pair when closure has no evidence photo", async () => {
    vi.mocked(getReport).mockResolvedValue(closedReport(false));
    vi.mocked(getTimeline).mockResolvedValue(closureTimeline);
    renderDetail();

    expect(await screen.findByText(en["receipt.title"])).toBeTruthy();
    expect(screen.queryByTestId("closure-receipt-photo-pair")).toBeNull();
    expect(screen.queryByRole("img", { name: en["receipt.beforeAlt"] })).toBeNull();
    expect(screen.queryByRole("img", { name: en["receipt.afterAlt"] })).toBeNull();
    expect(screen.queryByText(en["report.detail.waiting.verified_closed"])).toBeNull();
  });
});
