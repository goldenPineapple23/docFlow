import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The Team page (slice 5.8d, D-132) against a stubbed API: the admin sees who is
 * on the account, invites a reviewer, resends, and removes someone after a
 * second click; a reviewer sees neither the link nor the page. Who may do what
 * is proven against real Postgres in apps/api/tests/test_team_api.py.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function stubIdentity(page: Page, role: string) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({
      json: {
        email: "admin@example.test",
        tenant_id: "t1",
        tenant_name: "Acme Test Distributor",
        role,
        is_platform_admin: false,
        access_removed: false,
      },
    }),
  );
  await page.route(`${API}/allowance`, (route) =>
    route.fulfill({ json: { used: 1, allowance: 300, tier: "Starter", month: "2026-09", banner: null } }),
  );
  await page.route(`${API}/held`, (route) => route.fulfill({ json: { total: 0, groups: [], documents: [] } }));
}

const ADMIN = {
  user_id: "u-admin",
  email: "admin@example.test",
  full_name: null,
  role: "owner",
  invite_sent_at: "2026-09-01T10:00:00Z",
  signed_in: true,
  is_you: true,
  can_remove: false,
  can_resend: false,
};
const PENDING = {
  user_id: "u-pending",
  email: "pending@example.test",
  full_name: null,
  role: "reviewer",
  invite_sent_at: "2026-09-20T10:00:00Z",
  signed_in: false,
  is_you: false,
  can_remove: true,
  can_resend: true,
};

/** A small in-memory team, so the page's reload after each action shows the
 * change it asked for. */
async function stubTeam(page: Page) {
  let members = [ADMIN, PENDING];
  const calls: string[] = [];
  await page.route(`${API}/team`, (route) => route.fulfill({ json: { members } }));
  await page.route(`${API}/team/invite`, async (route: Route) => {
    const { email } = route.request().postDataJSON() as { email: string };
    calls.push(`invite ${email}`);
    if (email === "admin@example.test") {
      return route.fulfill({
        status: 409,
        json: {
          detail: {
            code: "TEAM-002",
            title: "This person is already on the team",
            message: "Someone with this email address already has access to this account.",
            action: "If they haven't signed in yet, use Resend invite next to their name instead.",
          },
        },
      });
    }
    members = [
      ...members,
      { ...PENDING, user_id: "u-new", email, invite_sent_at: new Date().toISOString() },
    ];
    return route.fulfill({ json: { user_id: "u-new", email, held: true, restored: false } });
  });
  await page.route(/\/team\/[^/]+\/(resend|remove)$/, async (route: Route) => {
    const [, userId, action] = route.request().url().match(/\/team\/([^/]+)\/(resend|remove)$/)!;
    calls.push(`${action} ${userId}`);
    const who = members.find((m) => m.user_id === userId)!;
    if (action === "remove") members = members.filter((m) => m.user_id !== userId);
    return route.fulfill({ json: { user_id: userId, email: who.email, held: false } });
  });
  return calls;
}

test("the admin sees the Team link; a reviewer does not", async ({ page }) => {
  await stubIdentity(page, "owner");
  await stubTeam(page);
  await page.goto("/team");
  const nav = page.getByRole("navigation");
  await expect(nav.getByRole("link", { name: "Team" })).toBeVisible();

  await stubIdentity(page, "reviewer");
  await page.reload();
  await expect(nav.getByRole("link", { name: "Upload" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Team" })).toHaveCount(0);
});

test("the team lists each person's role and whether they have signed in", async ({ page }) => {
  await stubIdentity(page, "owner");
  await stubTeam(page);
  await page.goto("/team");

  const rows = page.getByTestId("team-member");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("admin@example.test");
  await expect(rows.nth(0)).toContainText("(you)");
  await expect(rows.nth(0)).toContainText("Admin");
  await expect(rows.nth(0)).toContainText("Signed in");
  // The admin's own row offers nothing to click: not removable, nothing to resend.
  await expect(rows.nth(0).getByRole("button")).toHaveCount(0);

  await expect(rows.nth(1)).toContainText("Reviewer");
  await expect(rows.nth(1)).toContainText("Invite sent");
  await expect(rows.nth(1).getByTestId("resend")).toBeVisible();
  await expect(rows.nth(1).getByTestId("remove")).toBeVisible();
});

test("inviting a reviewer adds them, and says honestly when the email is held", async ({ page }) => {
  await stubIdentity(page, "owner");
  const calls = await stubTeam(page);
  await page.goto("/team");

  await page.getByTestId("invite-email").fill("new.reviewer@example.test");
  await page.getByTestId("invite-submit").click();

  await expect(page.getByTestId("team-notice")).toContainText("Invited new.reviewer@example.test.");
  await expect(page.getByTestId("team-notice")).toContainText("holding the invite");
  await expect(page.getByTestId("team-member")).toHaveCount(3);
  await expect(page.getByTestId("invite-email")).toHaveValue("");
  expect(calls).toEqual(["invite new.reviewer@example.test"]);
});

test("a refused invite shows the catalog's words and adds no one", async ({ page }) => {
  await stubIdentity(page, "owner");
  await stubTeam(page);
  await page.goto("/team");

  await page.getByTestId("invite-email").fill("admin@example.test");
  await page.getByTestId("invite-submit").click();

  const box = page.getByTestId("catalog-error");
  await expect(box).toContainText("This person is already on the team");
  await expect(box).toContainText("Resend invite");
  await expect(page.getByTestId("team-member")).toHaveCount(2);
});

test("removing asks once more, then takes the person off the list", async ({ page }) => {
  await stubIdentity(page, "owner");
  const calls = await stubTeam(page);
  await page.goto("/team");

  const pending = page.getByTestId("team-member").nth(1);
  await pending.getByTestId("remove").click();
  // Nothing has happened yet: the first click only asks.
  expect(calls).toEqual([]);
  await pending.getByRole("button", { name: "Cancel" }).click();
  await expect(pending.getByTestId("remove")).toBeVisible();

  await pending.getByTestId("remove").click();
  await pending.getByTestId("remove-confirm").click();

  await expect(page.getByTestId("team-notice")).toContainText("Removed pending@example.test");
  await expect(page.getByTestId("team-member")).toHaveCount(1);
  expect(calls).toEqual(["remove u-pending"]);
});

test("resending an invite says so", async ({ page }) => {
  await stubIdentity(page, "owner");
  const calls = await stubTeam(page);
  await page.goto("/team");

  await page.getByTestId("team-member").nth(1).getByTestId("resend").click();
  await expect(page.getByTestId("team-notice")).toContainText("Sent a new invite to pending@example.test.");
  expect(calls).toEqual(["resend u-pending"]);
});

test("a reviewer who types the address is told why, in the catalog's words", async ({ page }) => {
  await stubIdentity(page, "reviewer");
  await page.route(`${API}/team`, (route) =>
    route.fulfill({
      status: 403,
      json: {
        detail: {
          code: "AUTH-003",
          title: "This page is for account admins",
          message: "The dashboard and the people on this account are managed by your admin.",
          action: "Ask your account admin if you need something from it.",
        },
      },
    }),
  );
  await page.goto("/team");
  await expect(page.getByTestId("catalog-error")).toContainText("This page is for account admins");
  await expect(page.getByTestId("invite-form")).toHaveCount(0);
});

/** A signed-in browser, as far as the start page can tell: the Supabase client
 * keeps its session in localStorage under `sb-<project>-auth-token`, and the
 * project differs between machines, so this answers for any such key. */
async function fakeSession(page: Page) {
  await page.addInitScript(() => {
    const session = JSON.stringify({
      access_token: "test-token",
      refresh_token: "test-refresh",
      token_type: "bearer",
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: { id: "u-removed", email: "removed@example.test", aud: "authenticated" },
    });
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = function (key: string) {
      return /^sb-.*-auth-token$/.test(key) ? session : original.call(this, key);
    };
  });
}

test("a removed person who signs in is told why, instead of a dead end", async ({ page }) => {
  await fakeSession(page);
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({
      json: {
        email: "removed@example.test",
        tenant_id: null,
        tenant_name: null,
        role: null,
        is_platform_admin: false,
        access_removed: true,
        refusal: {
          code: "AUTH-004",
          title: "Your access to this account has ended",
          message: "Your account's admin has removed you from this DocFlow account.",
          action: "If you think this is a mistake, ask your account's admin to invite you again.",
        },
      },
    }),
  );
  await page.goto("/");

  const notice = page.getByTestId("access-removed");
  await expect(notice.getByTestId("catalog-error")).toContainText("Your access to this account has ended");
  await expect(notice).toContainText("Signed in as removed@example.test");
  await expect(notice.getByRole("button", { name: "Sign out" })).toBeVisible();
  // It stays put: nothing to forward a removed person to.
  await expect(page).toHaveURL(/\/$/);
});
