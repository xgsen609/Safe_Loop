import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";

import en from "../messages/en.json" with { type: "json" };
import zh from "../messages/zh-CN.json" with { type: "json" };
import { readRuntime, type E2ELocale, type E2EUser } from "./runtime";

const catalogues: Record<E2ELocale, Record<string, string>> = {
  en,
  "zh-CN": zh,
};

const untranslatedKeyPattern = /\b(?:action|alert|app|briefings|crew|dashboard|documents|error|inbox|learn|locale|notification|receipt|report|review|status|term|timeline|urgency|verification|work)\.[A-Za-z0-9_.-]+\b/u;
const rawErrorCodes = Object.keys(en)
  .filter((key) => key.startsWith("error."))
  .map((key) => key.slice("error.".length))
  .filter((code) => code.includes("_"));
const loopbackHosts = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

function copy(locale: E2ELocale, key: string): string {
  const value = catalogues[locale][key];
  if (!value) throw new Error(`Missing E2E catalogue key: ${key}`);
  return value;
}

function reporterFor(locale: E2ELocale): "reporterEn" | "reporterZh" {
  return locale === "en" ? "reporterEn" : "reporterZh";
}

async function newLocalContext(browser: Browser): Promise<BrowserContext> {
  const context = await browser.newContext();
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (
      loopbackHosts.has(url.hostname)
      || ["about:", "blob:", "data:"].includes(url.protocol)
    ) {
      await route.continue();
      return;
    }
    await route.abort("blockedbyclient");
    throw new Error(`Browser suite blocked an external request to ${url.origin}`);
  });
  return context;
}

function futureDateTimeInput(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 16);
}

function futureDateInput(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);
}

async function assertLocalisedSurface(page: Page): Promise<void> {
  const visibleText = await page.locator("body").innerText();
  expect(visibleText).not.toMatch(untranslatedKeyPattern);
  for (const code of rawErrorCodes) expect(visibleText).not.toContain(code);
}

async function login(page: Page, locale: E2ELocale, user: E2EUser): Promise<void> {
  await page.goto(`/${locale}/login`);
  await page.getByLabel(copy(locale, "app.email")).fill(user.email);
  await page.getByLabel(copy(locale, "app.password"), { exact: true }).fill(user.password);
  await page.getByRole("button", { name: copy(locale, "app.signIn") }).click();
  await expect(page).not.toHaveURL(new RegExp(`/${locale}/login(?:$|\\?)`));
}

async function reloadUntilText(
  page: Page,
  value: string,
  timeout = 45_000,
): Promise<void> {
  await expect
    .poll(
      async () => {
        await page.reload({ waitUntil: "domcontentloaded" });
        await page
          .getByText(value, { exact: true })
          .first()
          .waitFor({ state: "visible", timeout: 5_000 })
          .catch(() => undefined);
        return page.locator("body").innerText();
      },
      { intervals: [500, 1_000, 2_000], timeout },
    )
    .toContain(value);
}

async function reloadUntilSelector(
  page: Page,
  selector: string,
  timeout = 60_000,
): Promise<void> {
  await expect
    .poll(
      async () => {
        await page.reload({ waitUntil: "domcontentloaded" });
        await page
          .locator(selector)
          .first()
          .waitFor({ state: "attached", timeout: 5_000 })
          .catch(() => undefined);
        return page.locator(selector).count();
      },
      { intervals: [500, 1_000, 2_000], timeout },
    )
    .toBeGreaterThan(0);
}

async function openRolePages(browser: Browser, locale: E2ELocale) {
  const runtime = readRuntime();
  const reporterContext = await newLocalContext(browser);
  const reviewerContext = await newLocalContext(browser);
  const responsibleContext = await newLocalContext(browser);
  const reporterPage = await reporterContext.newPage();
  const reviewerPage = await reviewerContext.newPage();
  const responsiblePage = await responsibleContext.newPage();

  await login(reporterPage, locale, runtime.users[reporterFor(locale)]);
  await login(reviewerPage, locale, runtime.users.reviewer);
  await login(responsiblePage, locale, runtime.users.responsible);

  return {
    runtime,
    reporterContext,
    reviewerContext,
    responsibleContext,
    reporterPage,
    reviewerPage,
    responsiblePage,
  };
}

test.describe.serial("SafeLoop end-to-end contract", () => {
  for (const locale of ["en", "zh-CN"] as const) {
    test(`full corrective-action and lesson loop in ${locale}`, async ({ browser }) => {
      const pages = await openRolePages(browser, locale);
      const {
        runtime,
        reporterContext,
        reviewerContext,
        responsibleContext,
        reporterPage,
        reviewerPage,
        responsiblePage,
      } = pages;
      const description = locale === "en" ? "Unsafe" : "不安全";
      const clarificationAnswer = locale === "en"
        ? "The temporary edge protection is missing."
        : "临时边缘防护栏缺失。";
      const activity = locale === "en" ? "Inspection" : "现场检查";
      const location = `${runtime.runId}-${locale}-Level-6`;
      const deficiency = locale === "en"
        ? "The lower guardrail anchor still moves when pulled."
        : "拉动时，下方护栏锚点仍会移动。";

      try {
        await reporterPage.goto(`/${locale}/alerts`);
        await expect(reporterPage).toHaveURL(new RegExp(`/${locale}/not-authorised$`));
        await expect(
          reporterPage.getByText(copy(locale, "app.notAuthorised.title"), { exact: true }),
        ).toBeVisible();
        await assertLocalisedSurface(reporterPage);

        await reporterPage.goto(`/${locale}/report/new`);
        await reporterPage
          .getByRole("button", { name: new RegExp(copy(locale, "report.new.typeInstead"), "u") })
          .click();
        await reporterPage.getByLabel(copy(locale, "report.new.whatHappened")).fill(description);
        await reporterPage.getByLabel(copy(locale, "report.new.location")).fill(location);
        await reporterPage.getByRole("button", { name: copy(locale, "report.new.continue") }).click();
        await reporterPage.getByRole("button", { name: copy(locale, "report.new.dangerNo") }).click();
        await reporterPage.getByRole("button", { name: copy(locale, "report.new.continue") }).click();
        await reporterPage.getByLabel(copy(locale, "report.new.activity")).fill(activity);
        await reporterPage.getByRole("button", { name: copy(locale, "report.new.submit") }).click();
        await reporterPage.waitForURL(new RegExp(`/${locale}/report/[0-9a-f-]+$`));
        const reportId = new URL(reporterPage.url()).pathname.split("/").at(-1);
        if (!reportId) throw new Error("Submitted report URL did not contain an id");

        await reloadUntilText(reporterPage, copy(locale, "status.clarifying"));
        const humanRef = (await reporterPage.getByText(/^SL-\d{4}-\d{5}$/u).first().innerText()).trim();
        await expect(reporterPage.getByTestId("clarification-panel")).toBeVisible();
        await reporterPage
          .getByLabel(copy(locale, "report.clarification.answerLabel"))
          .fill(clarificationAnswer);
        await reporterPage
          .getByRole("button", { name: copy(locale, "report.clarification.submit") })
          .click();
        await reloadUntilText(reporterPage, copy(locale, "status.under_review"));
        await assertLocalisedSurface(reporterPage);

        await reviewerPage.goto(`/${locale}/review`);
        await reviewerPage
          .getByRole("checkbox", { name: copy(locale, "review.queue.manualTriage") })
          .check();
        await expect(
          reviewerPage.locator(`[data-report-id="${runtime.manualTriage.reportId}"]`),
        ).toBeVisible();
        await expect(reviewerPage.getByText(runtime.manualTriage.humanRef, { exact: true })).toBeVisible();
        await assertLocalisedSurface(reviewerPage);

        await reviewerPage.goto(`/${locale}/review`);
        const queueItem = reviewerPage.locator(`[data-report-id="${reportId}"]`);
        await expect(queueItem).toBeVisible();
        await queueItem.click();
        await expect(reviewerPage).toHaveURL(new RegExp(`/${locale}/review/${reportId}$`));

        await reviewerPage
          .getByRole("button", { name: copy(locale, "action.request_info") })
          .click();
        await expect(
          reviewerPage.getByRole("button", { name: copy(locale, "action.request_info") }),
        ).toBeDisabled();
        await reviewerPage
          .getByRole("button", { name: copy(locale, "review.detail.cancel") })
          .click();

        await reviewerPage
          .getByRole("button", { name: copy(locale, "action.approve_action") })
          .click();
        await reviewerPage.getByText(copy(locale, "review.detail.corrections"), { exact: true }).click();
        await reviewerPage
          .getByLabel(copy(locale, "review.detail.correctedAction"))
          .fill(locale === "en"
            ? "Install and pull-test temporary edge protection."
            : "安装临时边缘防护栏，并完成拉力测试。");
        await reviewerPage
          .getByLabel(copy(locale, "review.detail.correctionReason"))
          .fill(locale === "en"
            ? "The approved action must state the exact control and test."
            : "批准的行动必须写明具体防护措施和测试。" );
        await reviewerPage
          .getByLabel(copy(locale, "review.detail.assigneeId"))
          .fill(runtime.users.responsible.id);
        await reviewerPage
          .getByLabel(copy(locale, "review.detail.dueAt"))
          .fill(futureDateTimeInput(3));
        await reviewerPage
          .getByRole("button", { name: copy(locale, "review.detail.reviewApproval") })
          .click();
        await expect(
          reviewerPage.getByText(copy(locale, "review.detail.diffTitle"), { exact: true }),
        ).toBeVisible();
        await reviewerPage
          .getByRole("button", { name: copy(locale, "review.detail.confirmApproval") })
          .click();
        await expect(
          reviewerPage.getByText(copy(locale, "review.detail.savedTitle"), { exact: true }),
        ).toBeVisible();
        await expect(
          reviewerPage.getByText(copy(locale, "status.action_assigned"), { exact: true }),
        ).toBeVisible();
        await assertLocalisedSurface(reviewerPage);

        await responsiblePage.goto(`/${locale}/actions`);
        const assignedCard = responsiblePage.locator(`[data-report-id="${reportId}"]`);
        await expect(assignedCard).toBeVisible();
        await assignedCard
          .getByRole("button", { name: copy(locale, "work.submit.open") })
          .click();
        const emptyEvidenceButton = responsiblePage.getByRole("button", {
          name: copy(locale, "work.submit.send"),
        });
        await expect(emptyEvidenceButton).toBeDisabled();
        await responsiblePage
          .getByLabel(copy(locale, "work.submit.note"))
          .fill(locale === "en"
            ? "Installed the temporary edge protection and checked both anchors."
            : "已安装临时边缘防护栏，并检查两个锚点。" );
        await emptyEvidenceButton.click();
        await expect(
          responsiblePage.getByText(copy(locale, "work.submit.successTitle"), { exact: true }),
        ).toBeVisible();
        await assertLocalisedSurface(responsiblePage);

        await reviewerPage.goto(`/${locale}/verify/${reportId}`);
        await expect(
          reviewerPage.getByText(copy(locale, "status.action_submitted"), { exact: true }),
        ).toBeVisible();
        await reviewerPage
          .getByRole("button", { name: copy(locale, "action.verification_failed") })
          .click();
        await reviewerPage
          .getByLabel(copy(locale, "verification.notes.label"))
          .fill(locale === "en"
            ? "The evidence was checked at the reported location."
            : "已在报告地点检查提交的证据。" );
        await reviewerPage
          .getByLabel(copy(locale, "verification.failure.newDueLabel"))
          .fill(futureDateTimeInput(4));
        const sendBackButton = reviewerPage.getByRole("button", {
          name: copy(locale, "action.verification_failed"),
        });
        await reviewerPage
          .getByLabel(copy(locale, "verification.failure.reasonLabel"))
          .fill("   ");
        await expect(sendBackButton).toBeDisabled();
        await reviewerPage
          .getByLabel(copy(locale, "verification.failure.reasonLabel"))
          .fill(deficiency);
        await sendBackButton.click();
        await expect(
          reviewerPage.getByText(copy(locale, "verification.submit.successTitle"), { exact: true }),
        ).toBeVisible();
        await assertLocalisedSurface(reviewerPage);

        await responsiblePage.goto(`/${locale}/inbox`);
        const unreadSentBack = responsiblePage
          .getByRole("button")
          .filter({ hasText: copy(locale, "notification.sent_back") })
          .filter({ hasText: copy(locale, "inbox.unread") })
          .first();
        await expect(unreadSentBack).toBeVisible();
        await unreadSentBack.click();
        await expect(responsiblePage).toHaveURL(new RegExp(`/${locale}/actions`));
        const returnedCard = responsiblePage.locator(`[data-report-id="${reportId}"]`);
        await expect(returnedCard).toBeVisible();
        await expect(
          returnedCard.getByText(copy(locale, "action.returned.title"), { exact: true }),
        ).toBeVisible();
        await expect(returnedCard.getByText(deficiency, { exact: true })).toBeVisible();
        await returnedCard
          .getByRole("button", { name: copy(locale, "work.submit.again") })
          .click();
        await responsiblePage
          .getByLabel(copy(locale, "work.submit.note"))
          .fill(locale === "en"
            ? "Re-secured the lower anchor and completed a second pull test."
            : "已重新固定下方锚点，并完成第二次拉力测试。" );
        await responsiblePage
          .getByRole("button", { name: copy(locale, "work.submit.send") })
          .click();
        await expect(
          responsiblePage.getByText(copy(locale, "work.submit.successTitle"), { exact: true }),
        ).toBeVisible();
        await expect(
          responsiblePage.locator(`[data-report-id="${reportId}"]`),
        ).toHaveCount(0);
        await assertLocalisedSurface(responsiblePage);

        await reviewerPage.goto(`/${locale}/verify/${reportId}`);
        await reviewerPage
          .getByRole("button", { name: copy(locale, "action.verify_and_close") })
          .click();
        const checklist = reviewerPage.getByRole("checkbox");
        await expect(checklist).toHaveCount(3);
        for (let index = 0; index < 3; index += 1) await checklist.nth(index).check();
        await reviewerPage
          .getByLabel(copy(locale, "verification.notes.label"))
          .fill(locale === "en"
            ? "Both anchors held during the final pull test."
            : "两个锚点均通过最终拉力测试。" );
        await reviewerPage
          .getByRole("button", { name: copy(locale, "action.verify_and_close") })
          .click();
        await expect(
          reviewerPage.getByText(copy(locale, "verification.submit.successTitle"), { exact: true }),
        ).toBeVisible();

        await reviewerPage.goto(`/${locale}/briefings`);
        await reloadUntilSelector(
          reviewerPage,
          `[data-report-id="${reportId}"]`,
          60_000,
        );
        const briefingCard = reviewerPage.locator(`[data-report-id="${reportId}"]`);
        await briefingCard
          .getByRole("link", { name: copy(locale, "briefings.item.open") })
          .click();
        await expect(reviewerPage).toHaveURL(
          new RegExp(`/${locale}/briefings/[0-9a-f-]+$`, "u"),
          { timeout: 30_000 },
        );
        const validFromField = reviewerPage.getByLabel(
          copy(locale, "briefings.editor.validFrom"),
        );
        await expect(validFromField).toBeVisible({ timeout: 30_000 });
        await validFromField.fill(futureDateInput(0));
        await reviewerPage
          .getByLabel(copy(locale, "briefings.editor.validTo"))
          .fill(futureDateInput(30));
        const publishResponsePromise = reviewerPage.waitForResponse((response) =>
          response.request().method() === "POST"
          && /\/briefings\/manage\/[0-9a-f-]+\/publish$/u.test(new URL(response.url()).pathname),
        );
        await reviewerPage
          .getByRole("button", { name: copy(locale, "briefings.editor.publish") })
          .click();
        const publishResponse = await publishResponsePromise;
        expect(publishResponse.ok()).toBe(true);
        const published = await publishResponse.json() as { qr_token?: string };
        if (!published.qr_token) throw new Error("Published briefing did not return a QR token");
        await expect(
          reviewerPage.getByText(copy(locale, "briefings.editor.published"), { exact: true }),
        ).toBeVisible();
        await assertLocalisedSurface(reviewerPage);

        const publicContext = await newLocalContext(browser);
        try {
          const publicPage = await publicContext.newPage();
          await publicPage.goto(`/${locale}/b/${published.qr_token}`);
          const questions = publicPage.getByTestId("quiz-question");
          await expect(questions).toHaveCount(3);
          for (let index = 0; index < 3; index += 1) {
            const question = questions.nth(index);
            await question.getByRole("button").first().click();
            await expect(question.locator("[aria-live='polite']")).toBeVisible();
          }
          await assertLocalisedSurface(publicPage);

          await publicPage.goto(`/${locale}/b/${runtime.expiredBriefingToken}`);
          await expect(
            publicPage.getByText(copy(locale, "crew.inactive.title"), { exact: true }),
          ).toBeVisible();
          await assertLocalisedSurface(publicPage);
        } finally {
          await publicContext.close();
        }

        await reporterPage.goto(`/${locale}/report/${reportId}`);
        await expect(
          reporterPage.getByText(copy(locale, "receipt.title"), { exact: true }),
        ).toBeVisible();
        await expect(reporterPage.getByTestId("closure-receipt-summary")).toContainText(
          runtime.users.reviewer.displayName,
        );
        await assertLocalisedSurface(reporterPage);
        expect(humanRef).toMatch(/^SL-\d{4}-\d{5}$/u);
      } finally {
        await reporterContext.close();
        await reviewerContext.close();
        await responsibleContext.close();
      }
    });
  }

  for (const locale of ["en", "zh-CN"] as const) {
    test(`urgent alert tells the truth in ${locale}`, async ({ browser }) => {
      const runtime = readRuntime();
      const reporterContext = await newLocalContext(browser);
      const reviewerContext = await newLocalContext(browser);
      const reporterPage = await reporterContext.newPage();
      const reviewerPage = await reviewerContext.newPage();
      const location = `${runtime.runId}-${locale}-urgent-zone`;

      try {
        await login(reporterPage, locale, runtime.users[reporterFor(locale)]);
        await login(reviewerPage, locale, runtime.users.reviewer);
        await reporterPage.goto(`/${locale}/report/new`);
        await reporterPage
          .getByRole("button", { name: new RegExp(copy(locale, "report.new.typeInstead"), "u") })
          .click();
        await reporterPage
          .getByLabel(copy(locale, "report.new.whatHappened"))
          .fill(locale === "en" ? "A worker may fall." : "工人可能会坠落。" );
        await reporterPage.getByLabel(copy(locale, "report.new.location")).fill(location);
        await reporterPage
          .getByRole("button", { name: copy(locale, "report.new.continue") })
          .click();
        await reporterPage
          .getByRole("button", { name: copy(locale, "report.new.dangerYes") })
          .click();
        await expect(
          reporterPage.getByText(copy(locale, "alert.reporter.sent.title"), { exact: true }),
        ).toBeVisible();
        await expect(reporterPage.getByText(runtime.users.reviewer.displayName)).toHaveCount(0);
        await assertLocalisedSurface(reporterPage);

        await reviewerPage.goto(`/${locale}/review`);
        await expect(
          reviewerPage.getByText(copy(locale, "alert.banner.title"), { exact: true }),
        ).toBeVisible();
        await reviewerPage.goto(`/${locale}/alerts`);
        const alertCard = reviewerPage
          .locator("div.rounded-card")
          .filter({ hasText: location })
          .first();
        await expect(alertCard).toBeVisible();
        await alertCard
          .getByRole("button", { name: copy(locale, "alert.list.acknowledge") })
          .click();

        await expect
          .poll(() => reporterPage.locator("body").innerText(), {
            intervals: [500, 1_000, 2_000],
            timeout: 15_000,
          })
          .toContain(runtime.users.reviewer.displayName);
        await expect(
          reporterPage.getByText(copy(locale, "alert.reporter.sent.title"), { exact: true }),
        ).toHaveCount(0);
        await assertLocalisedSurface(reporterPage);
        await assertLocalisedSurface(reviewerPage);
      } finally {
        await reporterContext.close();
        await reviewerContext.close();
      }
    });
  }
});
