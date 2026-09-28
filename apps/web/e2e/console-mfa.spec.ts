import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The Console's MFA in the browser (DECISIONS.md D-151, D-177), against a
 * stubbed API: the banner while enforcement is off, the gate for a session
 * without an authenticator code, and the step-up dialog on a destructive
 * action. Supabase itself is unreachable here, so a code can never be
 * accepted -- which is exactly the wrong-code path (AUTH-008).
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const T = "dddddddd-dddd-dddd-dddd-dddddddddddd";

const AUTH_007 = {
  code: "AUTH-007",
  title: "Confirm with your authenticator code",
  message:
    "This action changes a customer's account or billing in a way that's hard to reverse, so it needs an authenticator code entered in the last five minutes. Yours is older than that, so nothing was done yet.",
  action: "Enter the current code from your authenticator app, then the action goes ahead.",
};

const VIEW = {
  usage: { used: 10, allowance: 300, tier: "Starter", month: "2026-09" },
  sender_settings: { strict_sender_mode: false, sender_allowlist: [] },
  expired_held: 0,
  limits: {
    monthly_ceiling: 900,
    monthly_ceiling_reached: false,
    daily_cost_ceiling_usd: "50",
    spend_today_usd: "0.01",
    daily_cost_ceiling_reached: false,
  },
  groups: [{ reason: "auth_fail", label: "Sender failed its authentication check", count: 1, title: "Held", message: "m" }],
  documents: [
    {
      id: "doc-1", original_filename: "po-one.pdf", content_sha256: "abcdef0123456789abcdef", sender_email: "ap@spoof.example.test",
      source: "email", quarantine_reason: "auth_fail", quarantined_at: "2026-09-23T10:00:00Z", created_at: "2026-09-23T10:00:00Z",
      subject: "Urgent PO", spf_result: "fail", dkim_result: "none", dmarc_result: "fail", file_type: "pdf",
      reason_label: "Sender failed its authentication check",
    },
  ],
};

async function stubConsole(page: Page, consoleMfa: { enforced: boolean; aal: string | null }) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({
      json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true, console_mfa: consoleMfa },
    }),
  );
  await page.route(`${API}/admin/tenants/${T}/overview`, (route) =>
    route.fulfill({ json: { tenant: { id: T, name: "Acme Test Quarantine" } } }),
  );
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
  await page.route(`${API}/admin/tenants/${T}/quarantine`, (route) => route.fulfill({ json: VIEW }));
}

test("while enforcement is off, every Console page says so and links to the setup", async ({ page }) => {
  await stubConsole(page, { enforced: false, aal: "aal1" });
  await page.goto(`/admin/tenants/${T}/quarantine`);

  const banner = page.getByTestId("console-mfa-banner");
  await expect(banner).toContainText("Authenticator codes aren't required yet");
  await expect(banner.getByRole("link", { name: "Set up your authenticator" })).toHaveAttribute("href", "/admin/security");
  await expect(page.getByTestId("usage")).toBeVisible(); // the page itself still works
});

test("with enforcement on and a confirmed sign-in, there is no banner and no gate", async ({ page }) => {
  await stubConsole(page, { enforced: true, aal: "aal2" });
  await page.goto(`/admin/tenants/${T}/quarantine`);

  await expect(page.getByTestId("usage")).toBeVisible();
  await expect(page.getByTestId("console-mfa-banner")).toHaveCount(0);
  await expect(page.getByTestId("console-mfa-gate")).toHaveCount(0);
});

test("with enforcement on, a sign-in without a code sees only the authenticator screen", async ({ page }) => {
  await stubConsole(page, { enforced: true, aal: "aal1" });
  await page.goto(`/admin/tenants/${T}/quarantine`);

  await expect(page.getByTestId("console-mfa-gate")).toBeVisible();
  await expect(page.getByTestId("authenticator-setup")).toBeVisible();
  await expect(page.getByTestId("usage")).toHaveCount(0);
  await expect(page.getByTestId("console-mfa-banner")).toHaveCount(0);
});

for (const [how, answer] of [
  ["refuses", (route: Route) => route.fulfill({ status: 403, json: { code: "no_authorization", msg: "refused" } })],
  ["doesn't answer", (route: Route) => route.abort("connectionrefused")],
] as const) {
  test(`an enrolment Supabase ${how} shows AUTH-009, not a network message`, async ({ page }) => {
    // Supabase is stubbed, never reached: the browser would otherwise send the
    // enrolment to the real project in .env, and the test would depend on it.
    let enrolAttempts = 0;
    await page.route(/\/auth\/v1\/factors/, (route) => {
      enrolAttempts += 1;
      return answer(route);
    });
    await stubConsole(page, { enforced: true, aal: "aal1" });
    await page.goto(`/admin/tenants/${T}/quarantine`);

    await page.getByRole("button", { name: "Set up an authenticator" }).click();
    const error = page.getByTestId("catalog-error");
    await expect(error).toContainText("The new authenticator couldn't be set up");
    await expect(error).toContainText("Any authenticator you already have still works.");
    await expect(error).not.toContainText("couldn't reach DocFlow");
    await expect(page.getByRole("button", { name: "Set up an authenticator" })).toBeEnabled();
    expect(enrolAttempts).toBe(1);
  });
}

test("a destructive action refused for want of a fresh code opens the code dialog; a wrong code is AUTH-008", async ({ page }) => {
  await stubConsole(page, { enforced: true, aal: "aal2" });
  let attempts = 0;
  await page.route(`${API}/admin/tenants/${T}/quarantine/clear`, (route) => {
    attempts += 1;
    route.fulfill({ status: 403, json: { detail: AUTH_007 } });
  });

  await page.goto(`/admin/tenants/${T}/quarantine`);
  await page.getByLabel("Select po-one.pdf").check();
  await page.getByRole("button", { name: /Clear selected \(1\)/ }).click();
  await page.getByTestId("clear-confirm-name").fill("Acme Test Quarantine");
  await page.getByTestId("clear-confirm").click();

  const dialog = page.getByTestId("step-up-dialog");
  await expect(dialog).toContainText(AUTH_007.title);
  await expect(dialog).toContainText(AUTH_007.action);

  await page.getByTestId("step-up-code").fill("123456");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByTestId("catalog-error")).toContainText("That code didn't match");

  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  // Cancelled: the action was not retried, and the refusal is what the page shows.
  expect(attempts).toBe(1);
  await expect(page.getByTestId("catalog-error")).toContainText(AUTH_007.title);
});
