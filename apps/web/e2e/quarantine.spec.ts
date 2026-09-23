import { expect, test, type Page } from "@playwright/test";

/**
 * Slice 5.7's screens (D-126), driven in the browser against a stubbed API:
 * the tenant's banner and "Held for review" page, and the Console's held
 * documents screen. The API's own behaviour (who may release what, received
 * order, the ceilings) is proven against real Postgres in
 * apps/api/tests/test_quarantine_api.py; this is what only a browser can show.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const T = "dddddddd-dddd-dddd-dddd-dddddddddddd";

const HELD = {
  total: 3,
  groups: [
    {
      code: "INT-003",
      reason: "unknown_sender_velocity",
      count: 2,
      can_release: true,
      title: "Held: unusual volume from new senders",
      message: "This account received more than 20 documents from unknown senders in the last hour.",
      action: "Open 'Held for review' to release the ones you recognize.",
    },
    {
      code: "INT-007",
      reason: "abuse_ceiling",
      count: 1,
      can_release: false,
      title: "Received and held for review",
      message: "DocFlow is reviewing unusual volume on this account, so new documents are being held.",
      action: "DocFlow has already been alerted and will release them once the volume is confirmed.",
    },
  ],
  documents: [
    { id: "doc-a", filename: "po-first.pdf", sender_email: "a@buyer.example.test", reason: "unknown_sender_velocity", received_at: "2026-09-23T10:00:00Z", can_release: true },
    { id: "doc-b", filename: "po-second.pdf", sender_email: "b@buyer.example.test", reason: "unknown_sender_velocity", received_at: "2026-09-23T10:05:00Z", can_release: true },
    { id: "doc-c", filename: "po-held-by-docflow.pdf", sender_email: "c@buyer.example.test", reason: "abuse_ceiling", received_at: "2026-09-23T10:10:00Z", can_release: false },
  ],
};

async function stubTenant(page: Page) {
  await page.route(`${API}/held`, (route) => route.fulfill({ json: HELD }));
  await page.route(`${API}/ignored-mail`, (route) =>
    route.fulfill({
      json: {
        mail: [
          {
            id: "m1",
            code: "INT-001",
            title: "No attachment to process",
            message: "This email didn't include an attachment DocFlow recognizes as a document.",
            action: "Resend with the purchase order attached as a PDF, Word, or Excel file.",
            sender_email: "nobody@example.test",
            subject: "Hello",
            filename: null,
            received_at: "2026-09-23T09:00:00Z",
          },
        ],
      },
    }),
  );
}

test("the Held for review page explains each hold and only lets the customer release their own", async ({ page }) => {
  await stubTenant(page);
  let released: unknown = null;
  await page.route(`${API}/held/release`, (route) => {
    released = route.request().postDataJSON();
    route.fulfill({ json: { released: ["doc-a", "doc-b"], skipped: [] } });
  });

  await page.goto("/held");

  const own = page.getByTestId("held-group-unknown_sender_velocity");
  await expect(own).toContainText("2 documents");
  await expect(own).toContainText("Open 'Held for review' to release the ones you recognize.");

  // A hold DocFlow reviews itself: no dead button, and it says what has been done.
  const theirs = page.getByTestId("held-group-abuse_ceiling");
  await expect(theirs).toContainText("nothing for you to do");
  await expect(page.getByLabel("Select po-held-by-docflow.pdf")).toBeDisabled();

  await expect(page.getByTestId("release-selected")).toBeDisabled();
  await page.getByLabel("Select po-first.pdf").check();
  await page.getByLabel("Select po-second.pdf").check();
  await page.getByTestId("release-selected").click();

  await expect(page.getByText("Released 2 documents")).toBeVisible();
  expect(released).toEqual({ document_ids: ["doc-a", "doc-b"] });

  await page.getByText("Ignored mail").click();
  await expect(page.getByText("No attachment to process")).toBeVisible();
});

test("the allowance banner and the held strip sit under the header and never block the queue", async ({ page }) => {
  await stubTenant(page);
  await page.route(`${API}/allowance`, (route) =>
    route.fulfill({
      json: {
        used: 312,
        allowance: 300,
        tier: "Starter",
        month: "2026-09",
        banner: {
          threshold_pct: 100,
          code: "LIM-002",
          title: "You've reached your monthly allowance",
          message: "You've used 312 of 300 documents included in Starter this month. Documents continue to process normally.",
          action: "Growth includes 1,000 documents per month -- contact us to upgrade.",
        },
      },
    }),
  );
  await page.route(/\/review\/documents/, (route) =>
    route.request().url().startsWith(API)
      ? route.fulfill({ json: { documents: [], total: 0, limit: 50, offset: 0 } })
      : route.continue(),
  );

  await page.goto("/review");

  const banner = page.getByTestId("allowance-banner");
  await expect(banner).toContainText("312 of 300 documents included in Starter");
  await expect(banner).toContainText("Documents continue to process normally");
  await expect(banner).toContainText("Growth includes 1,000");
  await expect(page.getByTestId("held-strip")).toContainText("3 documents are being held");
  await expect(page.getByRole("link", { name: "Open Held for review →" })).toHaveAttribute("href", "/held");
  // The queue itself is untouched.
  await expect(page.getByRole("heading", { name: "Purchase orders" })).toBeVisible();
});

test("a tenant under 80% sees no banner and no strip", async ({ page }) => {
  await page.route(`${API}/allowance`, (route) =>
    route.fulfill({ json: { used: 10, allowance: 300, tier: "Starter", month: "2026-09", banner: null } }),
  );
  await page.route(`${API}/held`, (route) => route.fulfill({ json: { total: 0, groups: [], documents: [] } }));
  await page.route(/\/review\/documents/, (route) =>
    route.request().url().startsWith(API)
      ? route.fulfill({ json: { documents: [], total: 0, limit: 50, offset: 0 } })
      : route.continue(),
  );
  await page.goto("/review");
  await expect(page.getByRole("heading", { name: "Purchase orders" })).toBeVisible();
  await expect(page.getByTestId("allowance-banner")).toHaveCount(0);
  await expect(page.getByTestId("held-strip")).toHaveCount(0);
});

// ── The Console ─────────────────────────────────────────────────────────────

const VIEW = {
  usage: { used: 312, allowance: 300, tier: "Starter", month: "2026-09" },
  sender_settings: { strict_sender_mode: false, sender_allowlist: [] },
  expired_held: 1,
  groups: [{ reason: "auth_fail", count: 2, title: "Held: sender couldn't be verified", message: "This email failed the sending domain's own authentication check." }],
  documents: [
    {
      id: "doc-1", original_filename: "po-one.pdf", content_sha256: "abcdef0123456789abcdef", sender_email: "ap@spoof.example.test",
      source: "email", quarantine_reason: "auth_fail", quarantined_at: "2026-09-23T10:00:00Z", created_at: "2026-09-23T10:00:00Z",
      subject: "Urgent PO", spf_result: "fail", dkim_result: "none", dmarc_result: "fail", file_type: "pdf",
    },
    {
      id: "doc-2", original_filename: "po-two.xlsx", content_sha256: "0123456789abcdef012345", sender_email: "ap@spoof.example.test",
      source: "email", quarantine_reason: "auth_fail", quarantined_at: "2026-09-23T10:01:00Z", created_at: "2026-09-23T10:01:00Z",
      subject: "Urgent PO", spf_result: "fail", dkim_result: "none", dmarc_result: "fail", file_type: "xlsx",
    },
  ],
};

async function stubConsole(page: Page) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
  await page.route(`${API}/admin/tenants/${T}/overview`, (route) =>
    route.fulfill({ json: { tenant: { id: T, name: "Acme Test Quarantine" } } }),
  );
  await page.route(/\/admin\/alerts/, (route) =>
    route.request().url().startsWith(API) ? route.fulfill({ json: { alerts: [] } }) : route.continue(),
  );
  await page.route(`${API}/admin/tenants/${T}/quarantine`, (route) => route.fulfill({ json: VIEW }));
}

test("the Console lists what the founder needs to decide and releases in bulk", async ({ page }) => {
  await stubConsole(page);
  let released: unknown = null;
  await page.route(`${API}/admin/tenants/${T}/quarantine/release`, (route) => {
    released = route.request().postDataJSON();
    route.fulfill({ json: { released: ["doc-1", "doc-2"], skipped: [] } });
  });

  await page.goto(`/admin/tenants/${T}/quarantine`);

  await expect(page.getByTestId("usage")).toContainText("312 / 300 documents (Starter)");
  await expect(page.getByText("past the retention period")).toBeVisible();
  const row = page.getByRole("row", { name: /po-one\.pdf/ });
  await expect(row).toContainText("ap@spoof.example.test");
  await expect(row).toContainText("Urgent PO");
  await expect(row).toContainText("fail / none / fail");
  await expect(row).toContainText("abcdef012345");

  await expect(page.getByTestId("release")).toBeDisabled();
  await page.getByRole("button", { name: "Select all" }).click();
  await page.getByTestId("release").click();
  await expect(page.getByText("Released 2.")).toBeVisible();
  expect(released).toEqual({ document_ids: ["doc-1", "doc-2"] });
});

test("clearing needs the tenant's name typed exactly", async ({ page }) => {
  await stubConsole(page);
  let cleared: unknown = null;
  await page.route(`${API}/admin/tenants/${T}/quarantine/clear`, (route) => {
    cleared = route.request().postDataJSON();
    route.fulfill({ json: { cleared: ["doc-1"] } });
  });

  await page.goto(`/admin/tenants/${T}/quarantine`);
  await page.getByLabel("Select po-one.pdf").check();
  await page.getByRole("button", { name: /Clear selected \(1\)/ }).click();

  await page.getByTestId("clear-confirm-name").fill("acme test quarantine"); // wrong case
  await expect(page.getByTestId("clear-confirm")).toBeDisabled();
  await page.getByTestId("clear-confirm-name").fill("Acme Test Quarantine");
  await expect(page.getByTestId("clear-confirm")).toBeEnabled();
  await page.getByTestId("clear-confirm").click();

  await expect(page.getByText("Cleared 1.")).toBeVisible();
  expect(cleared).toEqual({ document_ids: ["doc-1"], confirm_name: "Acme Test Quarantine" });
});

test("the approved-sender list is opt-in and explains that it holds rather than rejects", async ({ page }) => {
  await stubConsole(page);
  let saved: unknown = null;
  await page.route(`${API}/admin/tenants/${T}/sender-settings`, (route) => {
    saved = route.request().postDataJSON();
    route.fulfill({ json: { strict_sender_mode: true, sender_allowlist: ["buyer@example.com", "example.org"] } });
  });

  await page.goto(`/admin/tenants/${T}/quarantine`);
  await expect(page.getByTestId("strict-toggle")).not.toBeChecked(); // off by default
  await expect(page.getByText("never rejected")).toBeVisible();

  await page.getByTestId("strict-toggle").check();
  await page.getByTestId("allowlist").fill("buyer@example.com\n\n  example.org  \n");
  await page.getByTestId("save-senders").click();
  await expect(page.getByText("Approved-sender list is on.")).toBeVisible();
  expect(saved).toEqual({ strict_sender_mode: true, sender_allowlist: ["buyer@example.com", "example.org"] });
});

test("replacing the intake address asks first, then shows the new one and the grace date", async ({ page }) => {
  await stubConsole(page);
  await page.route(`${API}/admin/tenants/${T}/intake-address/rotate`, (route) =>
    route.fulfill({ json: { address: "orders+newtoken@intake.example.test", grace_ends_at: "2026-10-23T10:00:00Z" } }),
  );
  let asked = "";
  page.on("dialog", (dialog) => {
    asked = dialog.message();
    void dialog.accept();
  });

  await page.goto(`/admin/tenants/${T}/quarantine`);
  await page.getByTestId("rotate").click();
  await expect(page.getByText(/New address issued: orders\+newtoken@intake\.example\.test/)).toBeVisible();
  expect(asked).toContain("new intake address");
});
