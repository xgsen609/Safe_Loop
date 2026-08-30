"use client";

import {
  BellIcon,
  EnvelopeIcon,
  IdentificationIcon,
  LanguageIcon,
  ShieldCheckIcon,
} from "@heroicons/react/24/outline";
import Image from "next/image";
import { useTranslations } from "next-intl";

import safeLoopLogo from "../../app/login/safeloop-logo.png";
import type { CurrentProfile } from "../../lib/auth";
import { defaultLocale, isLocale, locales } from "../../lib/locales";
import { SignOutButton } from "../auth/SignOutButton";
import { useRoleNavigation } from "../navigation/useRoleNavigation";
import { AppShell } from "../ui/AppShell";
import { Card } from "../ui/Card";
import { LanguageSwitch } from "../ui/LanguageSwitch";

export function ProfilePage({
  requestedLocale,
  profile,
}: {
  requestedLocale: string;
  profile: CurrentProfile;
}) {
  const t = useTranslations();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const navItems = useRoleNavigation(locale, profile.role);
  const reviewerSurface = profile.role === "reviewer" || profile.role === "admin";

  return (
    <AppShell
      title={t("profile.title")}
      inboxHref={`/${locale}/inbox`}
      inboxLabel={t("app.inbox")}
      inboxIcon={<BellIcon className="h-6 w-6" />}
      unreadCount={0}
      pollStatus
      showUrgentAlerts={reviewerSurface}
      alertsHref={`/${locale}/alerts`}
      navItems={navItems}
      activeHref={`/${locale}/profile`}
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
      <section className="space-y-4 pb-8 pt-3">
        <Card className="profile-identity-card relative overflow-hidden text-ink-inverse">
          <div className="profile-identity-orb" aria-hidden="true" />
          <div className="relative flex items-center gap-4">
            <div className="flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-[24px] bg-surface p-1.5 shadow-safe">
              <Image
                src={safeLoopLogo}
                width={96}
                height={96}
                priority
                alt={t("login.logoAlt")}
                className="h-full w-full object-contain"
              />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold text-ink-inverse/70">{t("profile.signedIn")}</p>
              <h2 className="mt-1 break-words text-2xl font-bold text-ink-inverse">
                {profile.displayName}
              </h2>
              <div className="mt-3 inline-flex items-center gap-2 rounded-chip bg-ink-inverse/15 px-4 py-2 font-bold text-ink-inverse">
                <ShieldCheckIcon className="h-5 w-5" />
                <span>{t(`timeline.actor.${profile.role}`)}</span>
              </div>
            </div>
          </div>
          <p className="relative mt-5 border-t border-ink-inverse/15 pt-4 text-base leading-6 text-ink-inverse/75">
            {t(`profile.roleDescription.${profile.role}`)}
          </p>
        </Card>

        <Card className="space-y-1">
          <h2 className="pb-2 text-lg font-bold text-ink">{t("profile.accountDetails")}</h2>
          <dl className="divide-y divide-border">
            <div className="flex items-start gap-3 py-4">
              <IdentificationIcon className="mt-0.5 h-6 w-6 shrink-0 text-inkMuted" />
              <div className="min-w-0">
                <dt className="text-sm font-bold text-inkMuted">{t("profile.name")}</dt>
                <dd className="break-words text-base text-ink">{profile.displayName}</dd>
              </div>
            </div>
            <div className="flex items-start gap-3 py-4">
              <EnvelopeIcon className="mt-0.5 h-6 w-6 shrink-0 text-inkMuted" />
              <div className="min-w-0">
                <dt className="text-sm font-bold text-inkMuted">{t("profile.email")}</dt>
                <dd className="break-all text-base text-ink">
                  {profile.email ?? t("profile.notAvailable")}
                </dd>
              </div>
            </div>
            <div className="flex items-start gap-3 py-4">
              <ShieldCheckIcon className="mt-0.5 h-6 w-6 shrink-0 text-inkMuted" />
              <div className="min-w-0">
                <dt className="text-sm font-bold text-inkMuted">{t("profile.role")}</dt>
                <dd className="text-base text-ink">{t(`timeline.actor.${profile.role}`)}</dd>
              </div>
            </div>
            <div className="flex items-start gap-3 py-4">
              <LanguageIcon className="mt-0.5 h-6 w-6 shrink-0 text-inkMuted" />
              <div className="min-w-0">
                <dt className="text-sm font-bold text-inkMuted">{t("profile.preferredLanguage")}</dt>
                <dd className="text-base text-ink">
                  {profile.preferredLanguage === "zh-CN"
                    ? t("app.languageChinese")
                    : t("app.languageEnglish")}
                </dd>
              </div>
            </div>
          </dl>
        </Card>

        <Card className="space-y-3">
          <h2 className="text-lg font-bold text-ink">{t("profile.session")}</h2>
          <p className="text-sm leading-5 text-inkMuted">{t("profile.sessionDetail")}</p>
          <SignOutButton variant="text" />
        </Card>
      </section>
    </AppShell>
  );
}
