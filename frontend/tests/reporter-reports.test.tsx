import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReporterReportsPage } from "../components/reports/ReporterReportsPage";
import { listReports, type ReportListItem } from "../lib/reports";
import { reportStatus } from "../lib/stateMachine";
import en from "../messages/en.json";

vi.mock("../lib/reports", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/reports")>();
  return { ...original, listReports: vi.fn() };
});
vi.mock("../lib/notifications", () => ({
  listNotifications: vi.fn(async () => ({
    unread_count: 0,
    priority_unread_count: 0,
    unresolved_sent_back_count: 0,
    items: [],
  })),
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
vi.mock("next/navigation", () => ({
  usePathname: () => "/en/reports",
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

const report: ReportListItem = {
  id: "report-id",
  human_ref: "SL-2026-00001",
  status: reportStatus.under_review,
  urgency: "high",
  summary: "Loose guardrail at loading bay",
  location_text: "Level 6",
  created_at: "2026-08-22T01:00:00Z",
  thumbnail_caption: null,
  thumbnail_url: null,
  thumbnail_url_expires_at: null,
  action_id: null,
  action_text: null,
  action_status: null,
  action_due_at: null,
  completed_note: null,
  action_submitted_at: null,
  rework_count: 0,
  rework_attention: false,
  sent_back_unresolved: false,
  deficiency_reason: null,
  deficiency_notes: null,
  deficiency_created_at: null,
  deficiency_reviewer_name: null,
  previous_evidence: [],
};

describe("ReporterReportsPage", () => {
  beforeEach(() => {
    vi.mocked(listReports).mockReset();
    vi.mocked(listReports).mockResolvedValue({
      items: [report],
      next_cursor: null,
      counts: { overdue: 0, rework: 0 },
    });
  });
  afterEach(cleanup);

  it("lists only the role-scoped API results and provides every reporter tab", async () => {
    render(
      <NextIntlClientProvider locale="en" messages={expand(en)}>
        <ReporterReportsPage requestedLocale="en" />
      </NextIntlClientProvider>,
    );

    expect(await screen.findByText(report.summary)).toBeTruthy();
    expect(listReports).toHaveBeenCalledWith(
      { cursor: undefined, limit: 25, locale: "en" },
      "test-token",
    );
    expect(screen.getByRole("link", { name: en["app.home"] }).getAttribute("href"))
      .toBe("/en/report/new");
    expect(screen.getByRole("link", { name: en["app.myReports"] }).getAttribute("href"))
      .toBe("/en/reports");
    expect(screen.getByRole("link", { name: en["app.learn"] }).getAttribute("href"))
      .toBe("/en/learn");
    expect(screen.getAllByRole("link", { name: en["app.inbox"] })[0].getAttribute("href"))
      .toBe("/en/inbox");
    expect(screen.getByRole("link", { name: en["app.profile"] }).getAttribute("href"))
      .toBe("/en/profile");
    expect(screen.getByRole("button", { name: en["app.signOut"] })).toBeTruthy();
    expect(screen.getByText(report.summary).closest("a")?.getAttribute("href"))
      .toBe("/en/report/report-id");
  });
});
