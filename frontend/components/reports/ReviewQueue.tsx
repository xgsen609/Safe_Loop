"use client";

import {
  BellIcon,
  ClipboardDocumentCheckIcon,
  PhotoIcon,
} from "@heroicons/react/24/outline";
import { useTranslations } from "next-intl";
import Image from "next/image";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  defaultLocale,
  formatNumber,
  formatRelativeAge,
  isLocale,
  locales,
} from "../../lib/locales";
import {
  listReports,
  urgencyLevels,
  type ReportListItem,
  type Urgency,
} from "../../lib/reports";
import {
  reportStatuses,
  reportStatus,
  type ReportStatus,
} from "../../lib/stateMachine";
import { createClient } from "../../lib/supabase/browser";
import { useOperationsNavigation } from "../navigation/useOperationsNavigation";
import { AppShell } from "../ui/AppShell";
import { Banner } from "../ui/Banner";
import { SecondaryButton } from "../ui/Buttons";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { Field } from "../ui/Field";
import { LanguageSwitch } from "../ui/LanguageSwitch";
import { StatusChip } from "../ui/StatusChip";

const urgencyBorder: Record<Urgency, string> = {
  low: "",
  medium: "",
  high: "border-l-4 border-l-primary",
  critical: "border-l-4 border-l-danger",
};

const urgencyText: Record<Urgency, string> = {
  low: "text-inkMuted",
  medium: "text-inkMuted",
  high: "text-primaryStrong",
  critical: "text-danger",
};

export function ReviewQueue({ requestedLocale }: { requestedLocale: string }) {
  const t = useTranslations();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const navItems = useOperationsNavigation(locale);
  const [statusFilter, setStatusFilter] = useState<ReportStatus | "">("");
  const [urgencyFilter, setUrgencyFilter] = useState<Urgency | "">("");
  const [needsManualTriage, setNeedsManualTriage] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [items, setItems] = useState<ReportListItem[]>([]);
  const [queueCounts, setQueueCounts] = useState({ overdue: 0, rework: 0 });
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(
    async (cursor?: string) => {
      const append = Boolean(cursor);
      append ? setLoadingMore(true) : setLoading(true);
      setLoadFailed(false);
      try {
        const {
          data: { session },
        } = await createClient().auth.getSession();
        if (!session) throw new Error("session_required");
        const page = await listReports(
          {
            status: statusFilter || undefined,
            urgency: urgencyFilter || undefined,
            needsManualTriage,
            q: searchQuery || undefined,
            cursor,
            locale,
          },
          session.access_token,
        );
        setItems((current) => (append ? [...current, ...page.items] : page.items));
        setNextCursor(page.next_cursor);
        setQueueCounts(page.counts);
      } catch {
        setLoadFailed(true);
      } finally {
        append ? setLoadingMore(false) : setLoading(false);
      }
    },
    [locale, needsManualTriage, searchQuery, statusFilter, urgencyFilter],
  );

  useEffect(() => {
    void load();
  }, [load]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = searchInput.trim();
    if (query) {
      setStatusFilter("");
      setNeedsManualTriage(false);
    }
    setSearchQuery(query);
  }

  const languageSwitch = (
    <LanguageSwitch
      current={locale}
      label={t("app.language")}
      options={[
        { value: locales[0], label: t("app.languageEnglish") },
        { value: locales[1], label: t("app.languageChinese") },
      ]}
    />
  );
  return (
    <AppShell
      title={t("review.queue.title")}
      inboxHref={`/${locale}/inbox`}
      inboxLabel={t("app.inbox")}
      inboxIcon={<BellIcon className="h-6 w-6" />}
      unreadCount={0}
      pollStatus
      showUrgentAlerts
      alertsHref={`/${locale}/alerts`}
      navItems={navItems}
      activeHref={`/${locale}/review`}
      languageSwitch={languageSwitch}
    >
      <section className="space-y-4 pb-6 pt-3">
        <div className="grid grid-cols-2 gap-3">
          <Card className="space-y-1">
            <p className="text-2xl font-bold text-danger">
              {formatNumber(queueCounts.overdue, locale)}
            </p>
            <p className="text-sm font-bold text-inkMuted">
              {t("review.queue.overdueCount")}
            </p>
          </Card>
          <Card className="space-y-1">
            <p className="text-2xl font-bold text-warning">
              {formatNumber(queueCounts.rework, locale)}
            </p>
            <p className="text-sm font-bold text-inkMuted">
              {t("review.queue.reworkCount")}
            </p>
          </Card>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-bold text-inkMuted">
            <span>{t("review.queue.statusFilter")}</span>
            <select
              className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-base text-ink outline-none focus:border-primaryStrong focus:ring-2 focus:ring-primaryTint"
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value as ReportStatus | "");
                setNeedsManualTriage(false);
              }}
            >
              <option value="">{t("review.queue.allStatuses")}</option>
              {reportStatuses.map((status) => (
                <option key={status} value={status}>{t(`status.${status}`)}</option>
              ))}
            </select>
          </label>
          <label className="text-sm font-bold text-inkMuted">
            <span>{t("review.queue.urgencyFilter")}</span>
            <select
              className="mt-1 min-h-11 w-full rounded-control border border-border bg-surface px-3 text-base text-ink outline-none focus:border-primaryStrong focus:ring-2 focus:ring-primaryTint"
              value={urgencyFilter}
              onChange={(event) => setUrgencyFilter(event.target.value as Urgency | "")}
            >
              <option value="">{t("review.queue.allUrgencies")}</option>
              {urgencyLevels.map((urgency) => (
                <option key={urgency} value={urgency}>{t(`urgency.${urgency}`)}</option>
              ))}
            </select>
          </label>
        </div>

        <form className="grid grid-cols-[1fr_auto] items-end gap-3" onSubmit={search}>
          <Field
            label={t("review.queue.searchLabel")}
            placeholder={t("review.queue.searchPlaceholder")}
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
          />
          <SecondaryButton className="w-auto min-w-24" label={t("review.queue.search")} type="submit" />
        </form>

        <label className="flex min-h-11 items-center gap-3 rounded-control border border-border bg-surface px-3 text-base font-bold text-ink">
          <input
            checked={needsManualTriage}
            className="h-5 w-5 accent-primaryStrong"
            onChange={(event) => {
              const checked = event.target.checked;
              setNeedsManualTriage(checked);
              if (checked) setStatusFilter("");
            }}
            type="checkbox"
          />
          <span>{t("review.queue.manualTriage")}</span>
        </label>

        {loadFailed && (
          <Banner
            tone="warning"
            title={t("review.queue.loadFailedTitle")}
            detail={t("review.queue.loadFailedDetail")}
          />
        )}

        {loading ? (
          <p className="py-8 text-center text-base text-inkMuted" role="status">
            {t("review.queue.loading")}
          </p>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<ClipboardDocumentCheckIcon className="h-8 w-8" />}
            title={t("review.queue.emptyTitle")}
            detail={t("review.queue.emptyDetail")}
            action={loadFailed ? <SecondaryButton label={t("review.queue.retry")} onClick={() => void load()} /> : undefined}
          />
        ) : (
          <div className="space-y-3">
            {items.map((report) => (
              <Link
                className="block rounded-card outline-none focus:ring-2 focus:ring-primaryStrong focus:ring-offset-2 focus:ring-offset-bg"
                data-report-id={report.id}
                href={
                  report.status === reportStatus.action_submitted
                    ? `/${locale}/verify/${report.id}`
                    : `/${locale}/review/${report.id}`
                }
                key={report.id}
              >
                <Card className={`flex gap-4 ${urgencyBorder[report.urgency]}`}>
                  <div className="h-20 w-20 shrink-0 overflow-hidden rounded-tile bg-surfaceSunken">
                    {report.thumbnail_url ? (
                      <Image
                        className="h-full w-full object-cover"
                        src={report.thumbnail_url}
                        alt={report.thumbnail_caption?.trim() || t("review.queue.photoAlt")}
                        width={80}
                        height={80}
                        unoptimized
                      />
                    ) : (
                      <span className="grid h-full w-full place-items-center text-inkMuted" aria-label={t("review.queue.noPhoto")}>
                        <PhotoIcon className="h-7 w-7" />
                      </span>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <p className="truncate text-base font-bold text-ink">{report.summary}</p>
                      <p className={`shrink-0 text-xs font-bold uppercase tracking-wide ${urgencyText[report.urgency]}`}>
                        {t(`urgency.${report.urgency}`)}
                      </p>
                    </div>
                    <div className="mt-1 flex flex-wrap divide-x divide-border text-sm text-inkMuted">
                      <span className="pr-2 font-bold">{report.human_ref}</span>
                      <span className="px-2">{report.location_text?.trim() || t("review.queue.locationUnknown")}</span>
                      <span className="pl-2">{formatRelativeAge(report.created_at, locale)}</span>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <StatusChip status={report.status} label={t(`status.${report.status}`)} />
                      {report.rework_count > 0 && (
                        <span
                          className={`text-sm font-bold ${report.rework_attention ? "rounded-chip bg-dangerTint px-2 py-1 text-dangerStrong" : "text-danger"}`}
                        >
                          {t(
                            report.rework_attention
                              ? "review.queue.reworkAttention"
                              : "review.queue.rework",
                            { count: formatNumber(report.rework_count, locale) },
                          )}
                        </span>
                      )}
                    </div>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}

        {nextCursor && !loading && (
          <SecondaryButton
            label={loadingMore ? t("review.queue.loadingMore") : t("review.queue.loadMore")}
            disabled={loadingMore}
            onClick={() => void load(nextCursor)}
          />
        )}
      </section>
    </AppShell>
  );
}
