import { readFileSync } from "node:fs";
import type { APIRequestContext, Page } from "@playwright/test";
import { expect, test } from "./cspGuarded";
import { SEED_FILE } from "../playwright.live.config";
import { aStepAfter, totpCode } from "./totp";

/**
 * The Console behind real MFA, and a refused value named in the Console's own
 * words (D-241; D-240 item 9).
 *
 * Found in task 7's smoke test: "Create tenant" with an `@example.test` owner
 * got FastAPI's own 422, and the Console said "We couldn't reach DocFlow ...
 * DocFlow has been alerted". The stubbed suite could never see it: it decides
 * for itself what the API answers.
 *
 * Nothing is bypassed. The seed enrolled and verified an authenticator for a
 * CI-only platform admin through Supabase Auth's own API, on this runner's
 * stack; Supabase generated its secret. Here the admin signs in with a
 * password, the Console asks for a code, the code is computed from that
 * secret and typed in, and the API accepts the session as aal2. Separately,
 * the API itself refuses the same admin's password-only session with
 * AUTH-006: the web app's code screen is a courtesy, not the control.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

type ConsoleAdmin = { email: string; password: string; totp_secret: string; totp_step_used: number };
const seed: { tenant_name: string; console_admin: ConsoleAdmin | null } = JSON.parse(
  readFileSync(SEED_FILE, "utf8"),
);
const admin = seed.console_admin;

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} isn't set; the live suite needs it`);
  return value;
}

/** Off a stack on this machine the seed makes no admin (a platform admin on
 * staging is the founder alone), so this spec skips there. CI always has one. */
function needAdmin(): ConsoleAdmin {
  if (admin) return admin;
  if (process.env.CI) throw new Error("CI's seed made no Console admin; the Console spec can't run");
  test.skip(true, "no Console admin on this stack (only seeded on a stack on this machine)");
  throw new Error("unreachable");
}

function claims(token: string): Record<string, unknown> {
  return JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8"));
}

async function passwordOnlyToken(request: APIRequestContext, who: ConsoleAdmin): Promise<string> {
  const response = await request.post(
    `${required("NEXT_PUBLIC_SUPABASE_URL")}/auth/v1/token?grant_type=password`,
    {
      headers: { apikey: required("NEXT_PUBLIC_SUPABASE_ANON_KEY") },
      data: { email: who.email, password: who.password },
    },
  );
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()).access_token;
}

async function sessionToken(page: Page): Promise<string> {
  return page.evaluate(() => {
    const key = Object.keys(localStorage).find((k) => k.includes("auth-token"));
    if (!key) throw new Error("no Supabase session stored");
    return JSON.parse(localStorage.getItem(key) as string).access_token as string;
  });
}

function tenantList(request: APIRequestContext, token: string) {
  return request.get(`${required("NEXT_PUBLIC_API_BASE_URL")}/admin/tenants`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

async function consoleTenantNames(request: APIRequestContext, token: string): Promise<string[]> {
  const response = await tenantList(request, token);
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { name: string }[]).map((t) => t.name);
}

test("the API itself refuses the admin's password-only session with AUTH-006", async ({ request }) => {
  const who = needAdmin();
  const token = await passwordOnlyToken(request, who);
  expect(claims(token).aal).toBe("aal1");

  const response = await request.get(`${required("NEXT_PUBLIC_API_BASE_URL")}/admin/tenants`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status()).toBe(403);
  expect((await response.json()).detail.code).toBe("AUTH-006");
});

test("the admin passes a real authenticator challenge, and a refused owner address is named", async ({
  page,
  request,
}) => {
  const who = needAdmin();

  await page.goto("/login?next=/admin/tenants/new");
  await page.locator("#email").fill(who.email);
  await page.locator("#password").fill(who.password);
  await page.getByRole("button", { name: /sign in/i }).click();

  // The password alone gets the code screen, not the Console, and this very
  // session's tenant-list request is refused by the API with AUTH-006.
  await expect(page.getByTestId("console-mfa-gate")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("tenant-name")).toHaveCount(0);
  const passwordOnly = await sessionToken(page);
  expect(claims(passwordOnly).aal).toBe("aal1");
  const refused = await tenantList(request, passwordOnly);
  expect(refused.status()).toBe(403);
  expect((await refused.json()).detail.code).toBe("AUTH-006");

  const step = await aStepAfter(who.totp_step_used);
  await page.getByTestId("mfa-code").fill(totpCode(who.totp_secret, step));
  await page.getByRole("button", { name: "Confirm" }).click();

  // The page reloads with the upgraded session, and the same tenant-list
  // request now gets 200: this is the proof of aal2 at the API. The refused
  // create below shows it too: at aal1 the gate answers that same body with
  // AUTH-006 before any field is judged (test_validation_catalog.py).
  await expect(page.getByTestId("tenant-name")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("deal-preset-standard")).toBeChecked();
  const token = await sessionToken(page);
  expect(claims(token).aal).toBe("aal2");
  const before = await consoleTenantNames(request, token);
  // The list does carry names, or "not created" below would prove nothing.
  expect(before).toContain(seed.tenant_name);

  const name = `Acme Test Live Console ${who.totp_step_used}`;
  await page.getByTestId("tenant-name").fill(name);
  await page.getByTestId("tenant-owner-email").fill("owner@example.test");
  await page.getByTestId("tenant-create").click();

  const box = page.getByTestId("catalog-error");
  await expect(box.locator("p").first()).toHaveText("Owner email: This email address can't receive mail");
  await expect(box).toContainText("This address can't receive email, so DocFlow didn't save anything.");
  await expect(box).toContainText("Check the part after the @ and try again.");
  await expect(box).not.toContainText("couldn't reach DocFlow");
  await expect(page).toHaveURL(/\/admin\/tenants\/new$/);

  // Nothing was created.
  const after = await consoleTenantNames(request, token);
  expect(after).not.toContain(name);
  expect(after.length).toBe(before.length);
});
