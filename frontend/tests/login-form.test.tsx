import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import LoginForm from "../app/login/LoginForm";
import en from "../messages/en.json";
import zhCN from "../messages/zh-CN.json";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
}));
const signInWithPassword = vi.hoisted(() => vi.fn(async () => ({ error: null })));

vi.mock("next/navigation", () => ({
  usePathname: () => "/en/login",
  useRouter: () => navigation,
}));
vi.mock("../lib/supabase/browser", () => ({
  createClient: () => ({ auth: { signInWithPassword } }),
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

function renderLogin(locale: "en" | "zh-CN" = "en") {
  const messages = locale === "en" ? en : zhCN;
  render(
    <NextIntlClientProvider locale={locale} messages={expand(messages)}>
      <LoginForm />
    </NextIntlClientProvider>,
  );
}

describe("LoginForm", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("matches the branded English login content and keeps forgot password absent", () => {
    renderLogin();

    expect(screen.getByRole("heading", { name: /Safe\s*Loop/ })).toBeTruthy();
    expect(screen.getByText(en["login.tagline"])).toBeTruthy();
    expect(screen.getByRole("heading", { name: en["login.title"] })).toBeTruthy();
    expect(screen.getByLabelText(en["app.email"])).toBeTruthy();
    expect(screen.getByLabelText(en["app.password"])).toBeTruthy();
    expect(screen.getByRole("button", { name: en["app.signIn"] })).toBeTruthy();
    expect(screen.queryByText(/forgot/i)).toBeNull();
  });

  it("shows and hides the password without changing the entered value", async () => {
    const user = userEvent.setup();
    renderLogin();
    const password = screen.getByLabelText(en["app.password"]);

    await user.type(password, "safe-password");
    expect(password.getAttribute("type")).toBe("password");
    await user.click(screen.getByRole("button", { name: en["login.showPassword"] }));
    expect(password.getAttribute("type")).toBe("text");
    expect((password as HTMLInputElement).value).toBe("safe-password");
    await user.click(screen.getByRole("button", { name: en["login.hidePassword"] }));
    expect(password.getAttribute("type")).toBe("password");
  });

  it("keeps the existing email and password authentication flow", async () => {
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText(en["app.email"]), "reporter@example.test");
    await user.type(screen.getByLabelText(en["app.password"]), "safe-password");
    await user.click(screen.getByRole("button", { name: en["app.signIn"] }));

    expect(signInWithPassword).toHaveBeenCalledWith({
      email: "reporter@example.test",
      password: "safe-password",
    });
    expect(navigation.push).toHaveBeenCalledWith("/en");
  });

  it("renders localised Chinese content", () => {
    renderLogin("zh-CN");

    expect(screen.getByText(zhCN["login.tagline"])).toBeTruthy();
    expect(screen.getByRole("heading", { name: zhCN["login.title"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: zhCN["app.signIn"] })).toBeTruthy();
  });

  it("switches the login route from the segmented language control", async () => {
    const user = userEvent.setup();
    renderLogin();

    await user.click(screen.getByRole("button", { name: en["login.languageChinese"] }));

    expect(navigation.replace).toHaveBeenCalledWith("/zh-CN/login");
    expect(navigation.refresh).toHaveBeenCalled();
  });
});
