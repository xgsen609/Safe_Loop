import { cleanup, render, screen } from "@testing-library/react";
import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProfilePage } from "../components/profile/ProfilePage";
import type { AppRole, CurrentProfile } from "../lib/auth";
import en from "../messages/en.json";

vi.mock("../lib/notifications", () => ({
  listNotifications: vi.fn(async () => ({
    unread_count: 0,
    priority_unread_count: 0,
    unresolved_sent_back_count: 0,
    items: [],
  })),
}));
vi.mock("../lib/alerts", () => ({ listAlerts: vi.fn(async () => []) }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/en/profile",
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
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

const roleEntryLinks: Record<AppRole, { label: string; href: string }> = {
  reporter: { label: en["app.home"], href: "/en/report/new" },
  reviewer: { label: en["review.nav.queue"], href: "/en/review" },
  responsible: { label: en["action.nav.myWork"], href: "/en/actions" },
  crew: { label: en["app.learn"], href: "/en/learn" },
  admin: { label: en["review.nav.documents"], href: "/en/documents" },
};

function renderProfile(role: AppRole) {
  const profile: CurrentProfile = {
    id: `${role}-profile-id`,
    email: `${role}@example.test`,
    displayName: `${en[`timeline.actor.${role}`]} Account`,
    role,
    preferredLanguage: "en",
  };
  render(
    <NextIntlClientProvider locale="en" messages={expand(en)}>
      <ProfilePage requestedLocale="en" profile={profile} />
    </NextIntlClientProvider>,
  );
  return profile;
}

describe("ProfilePage", () => {
  afterEach(cleanup);

  for (const role of ["reporter", "reviewer", "responsible", "crew", "admin"] as const) {
    it(`shows the signed-in ${role} profile and role navigation`, () => {
      const profile = renderProfile(role);
      const entry = roleEntryLinks[role];

      expect(screen.getAllByText(profile.displayName).length).toBeGreaterThan(0);
      expect(screen.getByText(profile.email!)).toBeTruthy();
      expect(screen.getAllByText(en[`timeline.actor.${role}`]).length).toBeGreaterThan(0);
      expect(screen.getByText(en[`profile.roleDescription.${role}`])).toBeTruthy();
      expect(screen.getByRole("img", { name: en["login.logoAlt"] })).toBeTruthy();
      expect(screen.getByRole("link", { name: entry.label }).getAttribute("href"))
        .toBe(entry.href);
      expect(screen.getByRole("link", { name: en["app.profile"] }).getAttribute("href"))
        .toBe("/en/profile");
      expect(screen.getAllByRole("button", { name: en["app.signOut"] }).length)
        .toBeGreaterThan(0);
    });
  }
});
