import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewDecisionPage } from "../components/reports/ReviewDecisionPage";
import { defaultLocale, locales } from "../lib/locales";
import { mediaPhase } from "../lib/media";
import { listTechnicians } from "../lib/profiles";
import {
  getLessonDraftStatus,
  getReport,
  getTimeline,
  reviewReport,
  startLessonDraft,
  type ReportDetail,
} from "../lib/reports";
import { reportStatus } from "../lib/stateMachine";
import en from "../messages/en.json";
import zh from "../messages/zh-CN.json";

vi.mock("../lib/reports", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/reports")>();
  return {
    ...original,
    getReport: vi.fn(),
    getTimeline: vi.fn(),
    reviewReport: vi.fn(),
    getLessonDraftStatus: vi.fn(),
    startLessonDraft: vi.fn(),
  };
});
vi.mock("../lib/profiles", () => ({
  listTechnicians: vi.fn(),
}));
vi.mock("../lib/supabase/browser", () => ({
  createClient: () => ({
    auth: {
      getSession: async () => ({
        data: { session: { access_token: "test-token" } },
      }),
      getUser: async () => ({ data: { user: null } }),
    },
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/en/review/report-id",
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
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

const report: ReportDetail = {
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
  latest_draft: {
    id: "draft-id",
    version: 1,
    observed_facts: [
      "The Level 6 edge has no guardrail.",
      "The opening is beside material delivery work.",
    ],
    assumptions: ["The formwork crew owns this area."],
    missing_information: ["Whether work is scheduled below this level"],
    proposed_category: "work_at_height",
    proposed_urgency: "high",
    suggested_owner_role: "responsible",
    suggested_action: "Install secured guardrails before work resumes.",
    confidence: 0.82,
    needs_escalation: false,
    escalation_reason: null,
    citations: [
      {
        document_id: "20000000-0000-0000-0000-000000000001",
        doc_ref: "WAH-001",
        revision: "3",
        section: "4.2",
        page: 7,
        quote: "Install secured guardrails before work resumes. Keep the area clear.",
      },
    ],
    validation: "valid",
    validation_errors: [],
    created_at: "2026-08-22T01:01:00Z",
  },
  current_action: null,
  verifications: [],
  closure_receipt: null,
  available_transitions: [
    {
      event: "reject",
      target: reportStatus.rejected,
      requires_reason: true,
      review_decision: "reject",
    },
    {
      event: "request_info",
      target: reportStatus.info_requested,
      requires_reason: true,
      review_decision: "request_info",
    },
    {
      event: "escalate",
      target: reportStatus.escalated,
      requires_reason: true,
      review_decision: "escalate",
    },
    {
      event: "approve_action",
      target: reportStatus.action_assigned,
      requires_reason: false,
      review_decision: "approve",
    },
  ],
};

const timeline = [
  {
    id: "audit-id",
    event: "queue_for_review",
    actor_type: "system" as const,
    actor_role: null,
    source: reportStatus.ai_drafted,
    target: reportStatus.under_review,
    reason: null,
    created_at: "2026-08-22T01:02:00Z",
  },
];

function renderReview(locale = defaultLocale) {
  const messages = locale === defaultLocale ? en : zh;
  return render(
    <NextIntlClientProvider locale={locale} messages={expand(messages)}>
      <ReviewDecisionPage id="report-id" requestedLocale={locale} />
    </NextIntlClientProvider>,
  );
}

describe("ReviewDecisionPage", () => {
  beforeEach(() => {
    vi.mocked(getReport).mockReset();
    vi.mocked(getTimeline).mockReset();
    vi.mocked(reviewReport).mockReset();
    vi.mocked(getLessonDraftStatus).mockReset();
    vi.mocked(startLessonDraft).mockReset();
    vi.mocked(listTechnicians).mockReset();
    vi.mocked(getReport).mockResolvedValue(report);
    vi.mocked(getTimeline).mockResolvedValue(timeline);
    vi.mocked(listTechnicians).mockResolvedValue([
      {
        id: "00000000-0000-0000-0000-000000000004",
        display_name: "Ah Hock",
      },
      {
        id: "00000000-0000-0000-0000-000000000007",
        display_name: "Siti Aminah",
      },
    ]);
    vi.mocked(reviewReport).mockResolvedValue({
      review_id: "review-id",
      report_id: "report-id",
      status: reportStatus.info_requested,
      assignment_id: null,
      corrective_action_id: null,
    });
    vi.mocked(getLessonDraftStatus).mockResolvedValue({
      report_id: "report-id",
      status: "idle",
      started_at: null,
      finished_at: null,
      briefing_id: null,
    });
  });
  afterEach(cleanup);

  it("renders full report context and only server-returned review paths", async () => {
    renderReview();

    expect(await screen.findByText(report.human_ref)).toBeTruthy();
    expect(screen.getByText(report.description_original)).toBeTruthy();
    expect(screen.getByText(report.description_en!)).toBeTruthy();
    expect(screen.getByRole("img", { name: "Level 6 edge" })).toBeTruthy();
    expect(screen.getByText(en["timeline.event.queue_for_review"])).toBeTruthy();
    expect(screen.getByRole("button", { name: en["action.reject"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["action.approve_action"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["action.request_info"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["action.escalate"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: en["app.signOut"] })).toBeTruthy();
    expect(screen.getByText(en["review.draft.marker"])).toBeTruthy();
    expect(screen.getByText(en["review.draft.assumptionNotObserved"])).toBeTruthy();
    expect(screen.getByText(report.latest_draft!.observed_facts[0])).toBeTruthy();
    const assumption = screen.getByText(report.latest_draft!.assumptions[0]);
    expect(assumption.closest(".italic")).not.toBeNull();
    expect(screen.getByText(report.latest_draft!.missing_information[0])).toBeTruthy();
    expect(screen.getByText(en["review.draft.references"])).toBeTruthy();
    expect(
      screen.getByText(
        en["review.draft.referenceDocument"]
          .replace("{docRef}", "WAH-001")
          .replace("{revision}", "3"),
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Install secured guardrails before work resumes. Keep the area clear.",
      ),
    ).toBeTruthy();
  });

  it("renders report audio with the server-signed URL", async () => {
    vi.mocked(getReport).mockResolvedValue({
      ...report,
      media: [
        ...report.media,
        {
          id: "audio-id",
          storage_path: "private/report.mp4",
          mime_type: "audio/mp4",
          phase: mediaPhase.original,
          caption: null,
          retention_until: "2026-11-20T01:00:00Z",
          signed_url: "https://project.example/report.mp4?token=signed",
          signed_url_expires_at: "2026-08-22T01:10:00Z",
        },
      ],
    });
    renderReview();

    expect(await screen.findByText(en["report.media.audio"])).toBeTruthy();
    expect(document.querySelector("audio")?.getAttribute("src"))
      .toBe("https://project.example/report.mp4?token=signed");
  });

  it("does not invent a decision the server omitted", async () => {
    vi.mocked(getReport).mockResolvedValue({
      ...report,
      available_transitions: [report.available_transitions[0]],
    });
    renderReview();

    expect(
      await screen.findByRole("button", { name: en["action.reject"] }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: en["action.approve_action"] }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: en["action.request_info"] }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: en["action.escalate"] }),
    ).toBeNull();
  });

  it("requires the state-machine reason before submitting rejection", async () => {
    const user = userEvent.setup();
    renderReview();
    await user.click(
      await screen.findByRole("button", { name: en["action.reject"] }),
    );

    const submit = screen.getByRole<HTMLButtonElement>("button", {
      name: en["action.reject"],
    });
    expect(submit.disabled).toBe(true);
    await user.type(
      screen.getByLabelText(en["review.detail.reason"]),
      "This is not a site hazard.",
    );
    expect(submit.disabled).toBe(false);
    await user.click(submit);

    await waitFor(() =>
      expect(reviewReport).toHaveBeenCalledWith(
        "report-id",
        expect.objectContaining({
          decision: "reject",
          target: reportStatus.rejected,
          reason: "This is not a site hazard.",
        }),
        "test-token",
      ),
    );
  });

  it("collects action, correction reason, assignee and due date for approval", async () => {
    const user = userEvent.setup();
    renderReview();
    await user.click(
      await screen.findByRole("button", { name: en["action.approve_action"] }),
    );
    await user.click(screen.getByText(en["review.detail.corrections"]));
    const action = screen.getByLabelText<HTMLTextAreaElement>(
      en["review.detail.correctedAction"],
    );
    expect(action.value).toBe(report.latest_draft!.suggested_action);
    expect(
      screen.getByLabelText<HTMLInputElement>(
        en["review.detail.correctedCategory"],
      ).value,
    ).toBe(report.latest_draft!.proposed_category);
    expect(
      screen.getByLabelText<HTMLSelectElement>(
        en["review.detail.correctedUrgency"],
      ).value,
    ).toBe(report.latest_draft!.proposed_urgency);
    await user.clear(action);
    await user.type(
      action,
      "Install anchored guardrails before work resumes.",
    );
    await user.type(
      screen.getByLabelText(en["review.detail.correctionReason"]),
      "The action was missing.",
    );
    await user.selectOptions(
      screen.getByLabelText(en["review.detail.assignee"]),
      "00000000-0000-0000-0000-000000000004",
    );
    fireEvent.change(screen.getByLabelText(en["review.detail.dueAt"]), {
      target: { value: "2026-08-25T12:00" },
    });

    const review = screen.getByRole<HTMLButtonElement>("button", {
      name: en["review.detail.reviewApproval"],
    });
    expect(review.disabled).toBe(false);
    await user.click(review);

    expect(reviewReport).not.toHaveBeenCalled();
    expect(
      screen.getByText(
        en["review.detail.diffBefore"].replace(
          "{value}",
          report.latest_draft!.suggested_action!,
        ),
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        en["review.detail.diffAfter"].replace(
          "{value}",
          "Install anchored guardrails before work resumes.",
        ),
      ),
    ).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: en["review.detail.confirmApproval"] }),
    );

    await waitFor(() =>
      expect(reviewReport).toHaveBeenCalledWith(
        "report-id",
        expect.objectContaining({
          decision: "approve",
          target: reportStatus.action_assigned,
          corrected_action: "Install anchored guardrails before work resumes.",
          correction_reason: "The action was missing.",
          assignee_id: "00000000-0000-0000-0000-000000000004",
          due_at: expect.stringContaining("2026-08-25T"),
        }),
        "test-token",
      ),
    );
    const submitted = vi.mocked(reviewReport).mock.calls[0][1];
    expect(submitted.corrected_category).toBeUndefined();
    expect(submitted.corrected_urgency).toBeUndefined();
  });

  it("approves an unchanged draft only after showing an empty diff", async () => {
    const user = userEvent.setup();
    renderReview();
    await user.click(
      await screen.findByRole("button", { name: en["action.approve_action"] }),
    );
    await user.selectOptions(
      screen.getByLabelText(en["review.detail.assignee"]),
      "00000000-0000-0000-0000-000000000004",
    );
    fireEvent.change(screen.getByLabelText(en["review.detail.dueAt"]), {
      target: { value: "2026-08-25T12:00" },
    });

    await user.click(
      screen.getByRole("button", { name: en["review.detail.reviewApproval"] }),
    );

    expect(screen.getByText(en["review.detail.diffNoChanges"])).toBeTruthy();
    expect(reviewReport).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: en["review.detail.confirmApproval"] }),
    );

    await waitFor(() => expect(reviewReport).toHaveBeenCalledTimes(1));
    const submitted = vi.mocked(reviewReport).mock.calls[0][1];
    expect(submitted.corrected_category).toBeUndefined();
    expect(submitted.corrected_urgency).toBeUndefined();
    expect(submitted.corrected_action).toBeUndefined();
    expect(submitted.correction_reason).toBeUndefined();
  });

  it("shows the selected technician after approval is saved", async () => {
    const assignedReport: ReportDetail = {
      ...report,
      status: reportStatus.action_assigned,
      current_action: {
        id: "action-id",
        assignment_id: "assignment-id",
        assignee_id: "00000000-0000-0000-0000-000000000004",
        assignee_name: "Ah Hock",
        assignment_active: true,
        action_text: report.latest_draft!.suggested_action!,
        status: "assigned",
        rework_count: 0,
        due_at: "2026-08-25T12:00:00Z",
        completed_note: null,
        submitted_at: null,
      },
      available_transitions: [],
    };
    vi.mocked(getReport)
      .mockResolvedValueOnce(report)
      .mockResolvedValueOnce(assignedReport);
    vi.mocked(reviewReport).mockResolvedValue({
      review_id: "review-id",
      report_id: "report-id",
      status: reportStatus.action_assigned,
      assignment_id: "assignment-id",
      corrective_action_id: "action-id",
    });
    const user = userEvent.setup();
    renderReview();

    await user.click(
      await screen.findByRole("button", { name: en["action.approve_action"] }),
    );
    await user.selectOptions(
      screen.getByLabelText(en["review.detail.assignee"]),
      assignedReport.current_action!.assignee_id,
    );
    fireEvent.change(screen.getByLabelText(en["review.detail.dueAt"]), {
      target: { value: "2026-08-25T12:00" },
    });
    await user.click(
      screen.getByRole("button", { name: en["review.detail.reviewApproval"] }),
    );
    expect(
      screen.getByText(
        en["review.detail.confirmAssignee"].replace("{name}", "Ah Hock"),
      ),
    ).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: en["review.detail.confirmApproval"] }),
    );

    expect(await screen.findByText(en["review.detail.assignedTo"])).toBeTruthy();
    expect(screen.getByText("Ah Hock")).toBeTruthy();
  });

  it("renders the review context and decisions in Simplified Chinese", async () => {
    renderReview(locales[1]);

    expect(await screen.findByText(zh["review.detail.report"])).toBeTruthy();
    expect(screen.getByText(zh["timeline.event.queue_for_review"])).toBeTruthy();
    expect(screen.getByText(zh["review.draft.assumptionNotObserved"])).toBeTruthy();
    expect(screen.getByText(zh["review.draft.references"])).toBeTruthy();
    expect(
      screen.getByText(
        "Install secured guardrails before work resumes. Keep the area clear.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: zh["action.reject"] })).toBeTruthy();
  });

  it("takes the reviewer from a completed lesson draft to its publishing editor", async () => {
    vi.mocked(getReport).mockResolvedValue({
      ...report,
      status: reportStatus.lesson_drafted,
      available_transitions: [
        { event: "publish_lesson", target: reportStatus.lesson_published, requires_reason: false },
      ],
      current_briefing: {
        id: "briefing-id",
        version: 1,
        status: "draft",
        created_at: "2026-08-22T02:00:00Z",
        approved_at: null,
      },
    });

    renderReview();

    expect(await screen.findByText(en["workflow.next.lesson_drafted"])).toBeTruthy();
    expect(
      screen.getByRole("link", { name: en["workflow.next.reviewLesson"] }).getAttribute("href"),
    ).toBe(`/${defaultLocale}/briefings/briefing-id`);
  });

  it("shows live progress while AI is drafting the lesson", async () => {
    vi.mocked(getReport).mockResolvedValue({
      ...report,
      status: reportStatus.verified_closed,
      available_transitions: [],
    });
    vi.mocked(getLessonDraftStatus).mockResolvedValue({
      report_id: "report-id",
      status: "running",
      started_at: "2026-08-22T02:00:00Z",
      finished_at: null,
      briefing_id: null,
    });

    renderReview();

    expect(await screen.findByRole("progressbar")).toBeTruthy();
    expect(screen.getByText(en["workflow.next.draftingProgress"])).toBeTruthy();
    expect(screen.queryByRole("button", { name: en["workflow.next.retryLesson"] })).toBeNull();
  });

  it("localises validation error codes instead of showing machine codes", async () => {
    vi.mocked(getReport).mockResolvedValue({
      ...report,
      latest_draft: {
        ...report.latest_draft!,
        validation: "invalid",
        validation_errors: ["confidence_below_threshold"],
      },
    });

    renderReview(locales[1]);

    expect(await screen.findByText(zh["review.draft.validationFailed"])).toBeTruthy();
    expect(
      screen.getByText(zh["review.draft.validation.confidenceLow"]),
    ).toBeTruthy();
    expect(screen.queryByText("confidence_below_threshold")).toBeNull();
  });
});
