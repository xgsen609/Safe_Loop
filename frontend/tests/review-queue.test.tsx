import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewQueue } from "../components/reports/ReviewQueue";
import { defaultLocale, locales } from "../lib/locales";
import { listReports, type ReportListItem } from "../lib/reports";
import { reportStatus, reportStatuses } from "../lib/stateMachine";
import en from "../messages/en.json";
import zh from "../messages/zh-CN.json";

vi.mock("../lib/reports", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/reports")>();
  return { ...original, listReports: vi.fn() };
});
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
  usePathname: () => "/en/review",
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

const queueItem: ReportListItem = {
  id: "report-id",
  human_ref: "SL-2026-00001",
  status: reportStatus.under_review,
  urgency: "critical",
  summary: "Unprotected floor opening",
  location_text: "Level 14, Block C",
  created_at: new Date().toISOString(),
  thumbnail_caption: "Floor opening",
  thumbnail_url: "https://project.example/photo.jpg?token=signed",
  thumbnail_url_expires_at: new Date(Date.now() + 600_000).toISOString(),
  action_id: null,
  action_text: null,
  action_status: null,
  action_due_at: null,
  completed_note: null,
  action_submitted_at: null,
  rework_count: 2,
  rework_attention: true,
  sent_back_unresolved: false,
  deficiency_reason: null,
  deficiency_notes: null,
  deficiency_created_at: null,
  deficiency_reviewer_name: null,
  previous_evidence: [],
};

function renderQueue(locale = defaultLocale) {
  const messages = locale === defaultLocale ? en : zh;
  return render(
    <NextIntlClientProvider locale={locale} messages={expand(messages)}>
      <ReviewQueue requestedLocale={locale} />
    </NextIntlClientProvider>,
  );
}

describe("ReviewQueue", () => {
  beforeEach(() => {
    vi.mocked(listReports).mockReset();
    vi.mocked(listReports).mockResolvedValue({
      items: [queueItem],
      next_cursor: null,
      counts: { overdue: 2, rework: 3 },
    });
  });
  afterEach(cleanup);

  it("loads all reports by default and renders the required row fields", async () => {
    renderQueue();

    expect(await screen.findByText(queueItem.summary)).toBeTruthy();
    expect(screen.getByText(queueItem.human_ref)).toBeTruthy();
    expect(screen.getByText(queueItem.location_text!)).toBeTruthy();
    expect(screen.getByRole("img", { name: queueItem.thumbnail_caption! })).toBeTruthy();
    expect(screen.getAllByText(en["urgency.critical"])).toHaveLength(2);
    expect(screen.getAllByText(en["status.under_review"])).toHaveLength(2);
    expect(screen.getByText(en["review.queue.reworkAttention"].replace("{count}", "2"))).toBeTruthy();
    expect(screen.getByText(en["review.queue.overdueCount"])).toBeTruthy();
    expect(screen.getByText(en["review.queue.reworkCount"])).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: new RegExp(queueItem.summary) })
        .getAttribute("href"),
    ).toBe(`/${defaultLocale}/review/${queueItem.id}`);
    expect(listReports).toHaveBeenCalledWith(
      {
        status: undefined,
        urgency: undefined,
        needsManualTriage: false,
        q: undefined,
        cursor: undefined,
        locale: defaultLocale,
      },
      "test-token",
    );
  });

  it("keeps reviewers inside the PRD reviewer routes", async () => {
    renderQueue();
    await screen.findByText(queueItem.summary);

    const expectedLinks = [
      [en["review.nav.queue"], `/${defaultLocale}/review`],
      [en["review.nav.documents"], `/${defaultLocale}/documents`],
      [en["review.nav.briefings"], `/${defaultLocale}/briefings`],
      [en["review.nav.dashboard"], `/${defaultLocale}/dashboard`],
    ] as const;
    for (const [name, href] of expectedLinks) {
      expect(screen.getByRole("link", { name }).getAttribute("href")).toBe(href);
    }
    expect(screen.queryByRole("link", { name: en["review.nav.actions"] })).toBeNull();
    expect(screen.getByRole("link", { name: en["app.profile"] }).getAttribute("href"))
      .toBe(`/${defaultLocale}/profile`);
    expect(screen.queryByRole("link", { name: en["app.learn"] })).toBeNull();
    expect(screen.getByRole("button", { name: en["app.signOut"] })).toBeTruthy();
  });

  it("offers every generated status and refetches after filtering", async () => {
    renderQueue();
    await screen.findByText(queueItem.summary);
    const user = userEvent.setup();
    const filter = screen.getByRole("combobox", { name: en["review.queue.statusFilter"] });

    expect(filter.querySelectorAll("option")).toHaveLength(reportStatuses.length + 1);
    await user.selectOptions(filter, reportStatus.submitted);

    await waitFor(() =>
      expect(listReports).toHaveBeenLastCalledWith(
        {
          status: reportStatus.submitted,
          urgency: undefined,
          needsManualTriage: false,
          q: undefined,
          cursor: undefined,
          locale: defaultLocale,
        },
        "test-token",
      ),
    );
  });

  it("opens submitted corrective actions on the verification route", async () => {
    vi.mocked(listReports).mockResolvedValue({
      items: [{ ...queueItem, status: reportStatus.action_submitted }],
      next_cursor: null,
      counts: { overdue: 2, rework: 3 },
    });
    renderQueue();

    const link = await screen.findByRole("link", {
      name: new RegExp(queueItem.summary),
    });
    expect(link.getAttribute("href")).toBe(
      `/${defaultLocale}/verify/${queueItem.id}`,
    );
  });

  it("continues from the server cursor instead of using an offset", async () => {
    vi.mocked(listReports)
      .mockResolvedValueOnce({
        items: [queueItem],
        next_cursor: "opaque-next",
        counts: { overdue: 2, rework: 3 },
      })
      .mockResolvedValueOnce({
        items: [{ ...queueItem, id: "report-two", human_ref: "SL-2026-00002" }],
        next_cursor: null,
        counts: { overdue: 2, rework: 3 },
      });
    renderQueue();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: en["review.queue.loadMore"] }));

    expect(await screen.findByText("SL-2026-00002")).toBeTruthy();
    expect(listReports).toHaveBeenLastCalledWith(
      {
        status: undefined,
        urgency: undefined,
        needsManualTriage: false,
        q: undefined,
        cursor: "opaque-next",
        locale: defaultLocale,
      },
      "test-token",
    );
  });

  it("renders the queue controls and status in Simplified Chinese", async () => {
    renderQueue(locales[1]);

    expect(await screen.findByText(zh["review.queue.title"])).toBeTruthy();
    expect(await screen.findAllByText(zh["status.under_review"])).toHaveLength(2);
    expect(await screen.findAllByText(zh["urgency.critical"])).toHaveLength(2);
  });

  it("requests the reviewer-only manual triage queue", async () => {
    renderQueue();
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole("checkbox", {
        name: en["review.queue.manualTriage"],
      }),
    );

    await waitFor(() =>
      expect(listReports).toHaveBeenLastCalledWith(
        {
          status: undefined,
          urgency: undefined,
          needsManualTriage: true,
          q: undefined,
          cursor: undefined,
          locale: defaultLocale,
        },
        "test-token",
      ),
    );
  });

  it("searches across all statuses even after a status was selected", async () => {
    renderQueue();
    const user = userEvent.setup();
    const filter = await screen.findByRole("combobox", {
      name: en["review.queue.statusFilter"],
    });

    await user.selectOptions(filter, reportStatus.under_review);
    await user.type(
      screen.getByLabelText(en["review.queue.searchLabel"]),
      "SL-2026-00001",
    );
    await user.click(
      screen.getByRole("button", { name: en["review.queue.search"] }),
    );

    await waitFor(() => {
      expect((filter as HTMLSelectElement).value).toBe("");
      expect(listReports).toHaveBeenLastCalledWith(
        {
          status: undefined,
          urgency: undefined,
          needsManualTriage: false,
          q: "SL-2026-00001",
          cursor: undefined,
          locale: defaultLocale,
        },
        "test-token",
      );
    });
  });
});
