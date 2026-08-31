"use client";

import {
  BellIcon,
  ClipboardDocumentListIcon,
  PhotoIcon,
} from "@heroicons/react/24/outline";
import Image from "next/image";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

import {
  defaultLocale,
  formatRelativeAge,
  isLocale,
  locales,
} from "../../lib/locales";
import { listReports, type ReportListItem } from "../../lib/reports";
import { createClient } from "../../lib/supabase/browser";
import { useReporterNavigation } from "../navigation/useReporterNavigation";
import { AppShell } from "../ui/AppShell";
import { Banner } from "../ui/Banner";
import { SecondaryButton } from "../ui/Buttons";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { LanguageSwitch } from "../ui/LanguageSwitch";
import { StatusChip } from "../ui/StatusChip";

export function ReporterReportsPage({
  requestedLocale,
}: {
  requestedLocale: string;
}) {
  const t = useTranslations();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const navItems = useReporterNavigation(locale);
  const [items, setItems] = useState<ReportListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(async (cursor?: string) => {
    const append = Boolean(cursor);
    append ? setLoadingMore(true) : setLoading(true);
    setLoadFailed(false);
    try {
      const {
        data: { session },
      } = await createClient().auth.getSession();
      if (!session) throw new Error("session_required");
      const page = await listReports(
        { cursor, limit: 25, locale },
        session.access_token,
      );
      setItems((current) => (append ? [...current, ...page.items] : page.items));
      setNextCursor(page.next_cursor);
    } catch {
      setLoadFailed(true);
    } finally {
      append ? setLoadingMore(false) : setLoading(false);
    }
  }, [locale]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AppShell
      title={t("report.mine.title")}
      inboxHref={`/${locale}/inbox`}
      inboxLabel={t("app.inbox")}
      inboxIcon={<BellIcon className="h-6 w-6" />}
      unreadCount={0}
      pollStatus
      navItems={navItems}
      activeHref={`/${locale}/reports`}
      languageSwitch={(
        <LanguageSwitch
          current={locale}
          label={t("app.language")}
          options={[
            { value: locales[0], label: t("app.languageEnglish") },
            { value: locales[1], label: t("app.languageChinese") },
          ]}
        />
      )}
    >
      <section className="space-y-4 pb-6 pt-3">
        <p className="text-base leading-6 text-inkMuted">
          {t("report.mine.intro")}
        </p>

        {loadFailed && (
          <Banner
            tone="warning"
            title={t("report.mine.error.title")}
            detail={t("report.mine.error.detail")}
          />
        )}

        {loading ? (
          <p className="py-8 text-center text-base text-inkMuted" role="status">
            {t("report.mine.loading")}
          </p>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<ClipboardDocumentListIcon className="h-8 w-8" />}
            title={t("report.mine.empty.title")}
            detail={t("report.mine.empty.detail")}
            action={(
              <Link
                className="flex min-h-14 items-center justify-center rounded-control bg-primary px-5 text-base font-bold text-ink-inverse"
                href={`/${locale}/report/new`}
              >
                {t("report.mine.new")}
              </Link>
            )}
          />
        ) : (
          <div className="space-y-3">
            {items.map((report) => (
              <Link
                className="block rounded-card outline-none focus:ring-2 focus:ring-primaryStrong focus:ring-offset-2 focus:ring-offset-bg"
                data-report-id={report.id}
                href={`/${locale}/report/${report.id}`}
                key={report.id}
              >
                <Card className="flex gap-4">
                  <div className="h-20 w-20 shrink-0 overflow-hidden rounded-tile bg-surfaceSunken">
                    {report.thumbnail_url ? (
                      <Image
                        className="h-full w-full object-cover"
                        src={report.thumbnail_url}
                        alt={report.thumbnail_caption?.trim() || t("report.mine.photoAlt")}
                        width={80}
                        height={80}
                        unoptimized
                      />
                    ) : (
                      <span
                        aria-label={t("report.mine.noPhoto")}
                        className="grid h-full w-full place-items-center text-inkMuted"
                      >
                        <PhotoIcon className="h-7 w-7" />
                      </span>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <p className="truncate text-base font-bold text-ink">
                        {report.summary}
                      </p>
                      <span className="shrink-0 text-xs font-bold text-inkMuted">
                        {formatRelativeAge(report.created_at, locale)}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-inkMuted">
                      {t("report.mine.meta", {
                        reference: report.human_ref,
                        location: report.location_text?.trim() || t("report.mine.locationUnknown"),
                      })}
                    </p>
                    <div className="mt-3">
                      <StatusChip
                        status={report.status}
                        label={t(`status.${report.status}`)}
                      />
                    </div>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}

        {nextCursor && !loading && (
          <SecondaryButton
            label={loadingMore
              ? t("report.mine.loadingMore")
              : t("report.mine.loadMore")}
            disabled={loadingMore}
            onClick={() => void load(nextCursor)}
          />
        )}
      </section>
    </AppShell>
  );
}
