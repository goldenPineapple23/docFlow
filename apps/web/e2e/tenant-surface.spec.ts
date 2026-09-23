import { expect, test, type Page } from "@playwright/test";

/**
 * The tenant surface added in slice 5.8a (D-128), against a stubbed API: the
 * upload page, the customer's dashboard, and the navigation that reaches them.
 * The API's own behaviour (who may call what, what the counts mean) is proven
 * against real Postgres in apps/api/tests/test_home_api.py and test_roles.py.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function stubIdentity(page: Page, role: string) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({
      json: {
        email: "person@example.test",
        tenant_id: "t1",
        tenant_name: "Bella's Test Coffee",
        role,
        is_platform_admin: false,
      },
    }),
  );
  await page.route(`${API}/allowance`, (route) =>
    route.fulfill({ json: { used: 1, allowance: 300, tier: "Starter", month: "2026-09", banner: null } }),
  );
  await page.route(`${API}/held`, (route) => route.fulfill({ json: { total: 0, groups: [], documents: [] } }));
}

const HOME = {
  documents_by_status: { needs_review: 4, approved: 12, exported: 9, rejected: 1, failed: 2 },
  oldest_needs_review_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
  received_today: 3,
  this_month: { arrived: 30, counted: 27, approved: 12, exported: 9, median_hours_to_approval: 2.5 },
  allowance: { used: 27, allowance: 300, tier: "Starter", banner: null },
  held: {
    total: 2,
    groups: [
      {
        reason: "unknown_sender_velocity",
        count: 2,
        title: "Held: unusual volume from new senders",
        message: "This account received more than 20 documents from unknown senders in the last hour.",
      },
    ],
  },
  activity: [
    {
      at: new Date(Date.now() - 20 * 60_000).toISOString(),
      kind: "approved",
      document_id: "d1",
      document_name: "po-1.pdf",
      po_number: "PO-1001",
      by: "reviewer@example.test",
      by_docflow_support: false,
      detail: null,
    },
    {
      at: new Date(Date.now() - 90 * 60_000).toISOString(),
      kind: "edited",
      document_id: "d2",
      document_name: "po-2.pdf",
      po_number: "PO-1002",
      by: null,
      by_docflow_support: true,
      detail: "3 fields",
    },
  ],
};

// ── Navigation ──────────────────────────────────────────────────────────────

test("an admin sees the dashboard link; a reviewer does not", async ({ page }) => {
  await stubIdentity(page, "owner");
  await page.route(/\/review\/documents/, (route) =>
    route.request().url().startsWith(API)
      ? route.fulfill({ json: { documents: [], total: 0, limit: 50, offset: 0 } })
      : route.continue(),
  );
  await page.goto("/review");
  const nav = page.getByRole("navigation");
  await expect(nav.getByRole("link", { name: "Purchase orders" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Upload" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Held for review" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();

  await stubIdentity(page, "reviewer");
  await page.reload();
  await expect(nav.getByRole("link", { name: "Upload" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Dashboard" })).toHaveCount(0);
});

test("the dashboard's status tiles open that tab of the queue", async ({ page }) => {
  await stubIdentity(page, "owner");
  await page.route(`${API}/home`, (route) => route.fulfill({ json: HOME }));
  await page.route(/\/review\/documents/, (route) =>
    route.request().url().startsWith(API)
      ? route.fulfill({ json: { documents: [], total: 0, limit: 50, offset: 0 } })
      : route.continue(),
  );

  await page.goto("/dashboard");
  await page.getByRole("link", { name: /Couldn't be read/ }).click();

  await expect(page).toHaveURL(/\/review\?status=failed/);
  await expect(page.getByRole("button", { name: "Couldn't be read" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

// ── The dashboard ───────────────────────────────────────────────────────────

test("the dashboard shows the account's shape, and separates arrived from counted", async ({ page }) => {
  await stubIdentity(page, "owner");
  await page.route(`${API}/home`, (route) => route.fulfill({ json: HOME }));

  await page.goto("/dashboard");

  const tiles = page.getByTestId("status-tiles");
  await expect(tiles).toContainText("Needs review");
  await expect(tiles).toContainText("4");
  await expect(tiles).toContainText("12");

  // The two volume numbers are labelled differently, because they mean
  // different things: everything that arrived, and what the plan counts.
  const month = page.getByRole("heading", { name: "This month" }).locator("xpath=..");
  await expect(month).toContainText("Arrived");
  await expect(month).toContainText("30");
  await expect(month).toContainText("Counted toward your plan");
  await expect(month).toContainText("27 / 300");
  await expect(month).toContainText("2.5 h");

  await expect(page.getByTestId("held-tile")).toContainText("2 documents are being held");
  await expect(page.getByTestId("oldest-waiting")).toContainText("3 hours ago");

  const activity = page.getByTestId("activity");
  await expect(activity).toContainText("reviewer@example.test");
  await expect(activity).toContainText("PO-1001");
  // Work the founder did inside the account is labelled, never hidden.
  await expect(activity).toContainText("DocFlow support");
  await expect(activity).toContainText("(3 fields)");
});

test("a reviewer who reaches the dashboard is told why, from the catalog", async ({ page }) => {
  await stubIdentity(page, "reviewer");
  await page.route(`${API}/home`, (route) =>
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
  await page.goto("/dashboard");
  const box = page.getByTestId("catalog-error");
  await expect(box).toContainText("This page is for account admins");
  await expect(box).toContainText("Ask your account admin");
});

// ── Upload ──────────────────────────────────────────────────────────────────

test("uploading reports each file on its own, and a refusal costs the others nothing", async ({ page }) => {
  await stubIdentity(page, "reviewer");
  const sent: string[] = [];
  await page.route(`${API}/documents/upload`, async (route) => {
    const body = route.request().postData() ?? "";
    const name = /filename="([^"]+)"/.exec(body)?.[1] ?? "";
    sent.push(name);
    if (name.endsWith(".zip")) {
      return route.fulfill({
        status: 422,
        json: {
          detail: {
            code: "DOC-010",
            title: "We can't read archive files",
            message: "This is a .zip archive. DocFlow doesn't open archives.",
            action: "Send the purchase order itself as a separate attachment.",
          },
        },
      });
    }
    return route.fulfill({ json: { document_id: `doc-${name}`, status: "pending" } });
  });

  await page.goto("/upload");
  await page.getByTestId("choose-files").click();
  await page.locator('input[type="file"]').first().setInputFiles([
    { name: "po-one.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 fake") },
    { name: "orders.zip", mimeType: "application/zip", buffer: Buffer.from("PK fake") },
    { name: "po-two.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 fake") },
  ]);
  await expect(page.getByTestId("queued")).toContainText("orders.zip");

  await page.getByTestId("send").click();

  const results = page.getByTestId("results");
  await expect(results).toContainText("po-one.pdf");
  await expect(results).toContainText("po-two.pdf");
  await expect(results).toContainText("We can't read archive files");
  await expect(results).toContainText("Send the purchase order itself");
  await expect(page.getByText("2 of 3 files accepted")).toBeVisible();
  // Every file was attempted: one refusal does not abandon the rest.
  expect(sent).toEqual(["po-one.pdf", "orders.zip", "po-two.pdf"]);
});

test("a file accepted but held says so in the catalog's words, not as an error", async ({ page }) => {
  await stubIdentity(page, "reviewer");
  await page.route(`${API}/documents/upload`, (route) =>
    route.fulfill({
      json: {
        document_id: "doc-held",
        status: "quarantined",
        held: {
          code: "INT-007",
          title: "Received and held for review",
          message: "This account received an unusual volume of documents.",
          action: "DocFlow has been alerted and will release them once the volume is confirmed.",
        },
      },
    }),
  );

  await page.goto("/upload");
  await page.getByTestId("choose-files").click();
  await page.locator('input[type="file"]').first().setInputFiles([
    { name: "po.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 fake") },
  ]);
  await page.getByTestId("send").click();

  await expect(page.getByTestId("results")).toContainText("Received and held for review");
  await expect(page.getByTestId("results")).toContainText("DocFlow has been alerted");
  await expect(page.getByText("0 of 1 file accepted")).toBeVisible();
});
