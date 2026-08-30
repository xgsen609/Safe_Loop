"use client";

import { FormEvent, useState } from "react";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import {
  EnvelopeIcon,
  EyeIcon,
  EyeSlashIcon,
  LockClosedIcon,
} from "@heroicons/react/24/outline";
import { useLocale, useTranslations } from "next-intl";

import { createClient } from "../../lib/supabase/browser";
import {
  defaultLocale,
  isLocale,
  localeCookieName,
  locales,
  type Locale,
} from "../../lib/locales";
import safeLoopLogo from "./safeloop-logo.png";

export default function LoginForm() {
  const pathname = usePathname();
  const router = useRouter();
  const requestedLocale = useLocale();
  const locale = isLocale(requestedLocale) ? requestedLocale : defaultLocale;
  const t = useTranslations();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function switchLocale(nextLocale: Locale) {
    if (nextLocale === locale) return;
    document.cookie = `${localeCookieName}=${encodeURIComponent(nextLocale)}; Path=/; Max-Age=31536000; SameSite=Lax`;
    const segments = pathname.split("/");
    segments[1] = nextLocale;
    router.replace(segments.join("/") || `/${nextLocale}/login`);
    router.refresh();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const { error: signInError } = await createClient().auth.signInWithPassword({ email, password });
    if (signInError) {
      setError(t("app.loginFailed"));
      return;
    }
    router.push(`/${locale}`);
    router.refresh();
  }

  return (
    <main className="min-h-screen bg-bg pb-6 sm:pb-12">
      <div className="mx-auto flex w-full max-w-[520px] flex-col px-6 pb-6 pt-4 sm:px-7 sm:pb-10 sm:pt-10">
        <div
          className="ml-auto flex rounded-chip bg-surfaceSunken p-1"
          aria-label={t("app.language")}
        >
          {locales.toReversed().map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={option === locale}
              onClick={() => switchLocale(option)}
              className={`min-h-11 min-w-[88px] rounded-chip px-4 text-base font-bold transition-colors ${
                option === locale
                  ? "bg-surface text-ink shadow-safe"
                  : "text-inkMuted"
              }`}
            >
              {option === "en" ? t("app.languageEnglish") : t("login.languageChinese")}
            </button>
          ))}
        </div>

        <header className="mt-4 flex flex-col items-center text-center sm:mt-8">
          <div className="flex h-20 w-20 items-center justify-center overflow-hidden rounded-[24px] bg-surface p-1.5 shadow-safe sm:h-28 sm:w-28 sm:rounded-[32px] sm:p-2">
            <Image
              src={safeLoopLogo}
              width={112}
              height={112}
              priority
              alt={t("login.logoAlt")}
              className="h-full w-full object-contain"
            />
          </div>
          <h1 className="mt-3 text-[2rem] font-bold leading-none tracking-tight sm:mt-4 sm:text-[2.4rem]">
            <span>{t("login.brandSafe")}</span><span className="text-primary">{t("login.brandLoop")}</span>
          </h1>
          <p className="mt-2 text-base text-inkMuted sm:mt-3 sm:text-lg">{t("login.tagline")}</p>
        </header>

        <section className="mt-5 rounded-[24px] border border-border bg-surface px-5 py-5 shadow-safe sm:mt-8 sm:rounded-[28px] sm:px-7 sm:py-8">
          <h2 className="text-2xl font-bold leading-tight sm:text-[1.75rem]">{t("login.title")}</h2>
          <p className="mt-1 text-sm text-inkMuted sm:text-lg">{t("login.subtitle")}</p>

          <form className="mt-5 space-y-4 sm:mt-7 sm:space-y-6" onSubmit={submit}>
            <label className="block">
              <span className="text-xs font-bold uppercase tracking-wide sm:text-sm">{t("app.email")}</span>
              <span className="mt-1.5 flex min-h-14 items-center gap-3 rounded-control border border-border bg-bg px-4 focus-within:border-primary focus-within:ring-2 focus-within:ring-primaryTint sm:mt-2 sm:min-h-16">
                <EnvelopeIcon className="h-5 w-5 shrink-0 text-inkMuted sm:h-6 sm:w-6" aria-hidden="true" />
                <input
                  className="min-w-0 flex-1 bg-transparent text-base text-ink outline-none placeholder:text-inkMuted sm:text-lg"
                  type="email"
                  autoComplete="email"
                  placeholder={t("login.emailPlaceholder")}
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </span>
            </label>

            <div className="block">
              <label
                className="text-xs font-bold uppercase tracking-wide sm:text-sm"
                htmlFor="login-password"
              >
                {t("app.password")}
              </label>
              <div className="mt-1.5 flex min-h-14 items-center gap-3 rounded-control border border-border bg-bg px-4 focus-within:border-primary focus-within:ring-2 focus-within:ring-primaryTint sm:mt-2 sm:min-h-16">
                <LockClosedIcon className="h-5 w-5 shrink-0 text-inkMuted sm:h-6 sm:w-6" aria-hidden="true" />
                <input
                  id="login-password"
                  className="min-w-0 flex-1 bg-transparent text-base text-ink outline-none sm:text-lg"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
                <button
                  type="button"
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-chip text-inkMuted transition-colors hover:bg-surfaceSunken hover:text-ink"
                  aria-label={showPassword ? t("login.hidePassword") : t("login.showPassword")}
                  onClick={() => setShowPassword((current) => !current)}
                >
                  {showPassword
                    ? <EyeSlashIcon className="h-6 w-6" aria-hidden="true" />
                    : <EyeIcon className="h-6 w-6" aria-hidden="true" />}
                </button>
              </div>
            </div>

            {error && <p className="rounded-control bg-dangerTint p-3 font-bold text-danger" role="alert">{error}</p>}

            <button
              className="min-h-14 w-full rounded-control bg-primary px-4 text-lg font-bold text-ink-inverse shadow-safe transition-colors hover:bg-primaryStrong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary sm:min-h-16 sm:text-xl"
              type="submit"
            >
              {t("app.signIn")}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
