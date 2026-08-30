import type { SupabaseClient } from "@supabase/supabase-js";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "../lib/api";
import { defaultLocale } from "../lib/locales";
import {
  fileReport,
  listReports,
  retryReportIntake,
  reviewReport,
  verifyReport,
  type NewReportInput,
  type ReviewInput,
} from "../lib/reports";
import { reportStatus } from "../lib/stateMachine";

vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));

const input: NewReportInput = {
  description_original: "Loose edge protection",
  lang_original: defaultLocale,
  location_text: "Level 6",
  activity: "Material delivery",
  level_or_zone: null,
  grid_ref: null,
  is_confidential: false,
};

describe("fileReport", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("creates the draft before transitioning it to submitted", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: "report-id" })
      .mockResolvedValueOnce({
        id: "report-id",
        human_ref: "SL-2026-00001",
        status: reportStatus.submitted,
      });

    await expect(fileReport(input, "test-token")).resolves.toMatchObject({
      status: reportStatus.submitted,
    });
    expect(apiFetch).toHaveBeenNthCalledWith(1, "/reports", "test-token", {
      method: "POST",
      body: JSON.stringify(input),
    });
    expect(apiFetch).toHaveBeenNthCalledWith(
      2,
      "/reports/report-id/transition",
      "test-token",
      {
        method: "POST",
        body: JSON.stringify({
          target: reportStatus.submitted,
          confirmed_text: input.description_original,
        }),
      },
    );
  });

  it("uploads and registers a selected photo before submission", async () => {
    const upload = vi.fn(async () => ({ data: {}, error: null }));
    const client = {
      storage: {
        from: () => ({
          upload,
          remove: vi.fn(async () => ({ data: [], error: null })),
        }),
      },
    } as unknown as SupabaseClient;
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: "report-id" })
      .mockResolvedValueOnce({ id: "media-id" })
      .mockResolvedValueOnce({
        id: "report-id",
        human_ref: "SL-2026-00001",
        status: reportStatus.submitted,
      });

    await fileReport(input, "test-token", {
      client,
      file: new File(["photo"], "hazard.jpg", { type: "image/jpeg" }),
      userId: "reporter-id",
      caption: "Loose edge protection",
      downscale: async (file) => file,
    });

    expect(apiFetch).toHaveBeenCalledTimes(3);
    expect(vi.mocked(apiFetch).mock.calls[1][0]).toBe("/reports/report-id/media");
    expect(vi.mocked(apiFetch).mock.calls[2][0]).toBe("/reports/report-id/transition");
    expect(vi.mocked(apiFetch).mock.invocationCallOrder[0]).toBeLessThan(
      upload.mock.invocationCallOrder[0],
    );
    expect(upload.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(apiFetch).mock.invocationCallOrder[2],
    );
  });

  it("submits the confirmed text with its server-issued transcript id", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: "report-id" })
      .mockResolvedValueOnce({
        id: "report-id",
        human_ref: "SL-2026-00001",
        status: reportStatus.submitted,
      });

    await fileReport(
      input,
      "test-token",
      undefined,
      undefined,
      "transcript-id",
      "audio-id",
    );

    expect(apiFetch).toHaveBeenNthCalledWith(
      2,
      "/reports/report-id/transition",
      "test-token",
      {
        method: "POST",
        body: JSON.stringify({
          target: reportStatus.submitted,
          confirmed_text: "Loose edge protection",
          transcript_id: "transcript-id",
          audio_media_id: "audio-id",
        }),
      },
    );
  });

  it("passes every queue filter through one keyset-paginated request", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ items: [], next_cursor: null });

    await listReports(
      {
        status: reportStatus.under_review,
        urgency: "critical",
        assignee: "00000000-0000-0000-0000-000000000004",
        needsManualTriage: true,
        q: "Tower A",
        cursor: "opaque-cursor",
        limit: 25,
      },
      "test-token",
    );

    expect(apiFetch).toHaveBeenCalledWith(
      "/reports?status=under_review&urgency=critical&assignee=00000000-0000-0000-0000-000000000004&needs_manual_triage=true&q=Tower+A&cursor=opaque-cursor&limit=25",
      "test-token",
    );
  });

  it("retries a stranded AI intake through the synchronous endpoint", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      report_id: "report-id",
      status: reportStatus.clarifying,
    });

    await retryReportIntake("report-id", "test-token");

    expect(apiFetch).toHaveBeenCalledWith(
      "/reports/report-id/intake/retry",
      "test-token",
      { method: "POST" },
    );
  });

  it("sends the complete review payload to the atomic endpoint", async () => {
    const review: ReviewInput = {
      decision: "approve",
      target: reportStatus.action_assigned,
      corrected_action: "Install secured guardrails.",
      correction_reason: "The action was missing.",
      assignee_id: "00000000-0000-0000-0000-000000000004",
      due_at: "2026-08-25T04:00:00.000Z",
    };
    vi.mocked(apiFetch).mockResolvedValueOnce({
      review_id: "review-id",
      report_id: "report-id",
      status: reportStatus.action_assigned,
      assignment_id: "assignment-id",
      corrective_action_id: "action-id",
    });

    await reviewReport("report-id", review, "test-token");

    expect(apiFetch).toHaveBeenCalledWith(
      "/reports/report-id/review",
      "test-token",
      { method: "POST", body: JSON.stringify(review) },
    );
  });

  it("sends verification evidence and the new deadline to the atomic endpoint", async () => {
    const verification = {
      passed: false,
      checklist: { hazard_removed: false },
      notes: "The lower anchor was pull-tested.",
      reason: "The lower anchor still moves when pulled.",
      new_due_at: "2026-08-30T09:00:00.000Z",
    };
    vi.mocked(apiFetch).mockResolvedValueOnce({
      verification_id: "verification-id",
      status: reportStatus.action_assigned,
    });

    await verifyReport("report-id", verification, "test-token");

    expect(apiFetch).toHaveBeenCalledWith(
      "/reports/report-id/verify",
      "test-token",
      { method: "POST", body: JSON.stringify(verification) },
    );
  });
});
