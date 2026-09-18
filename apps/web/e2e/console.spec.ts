import { expect, test, type Page } from "@playwright/test";

/**
 * The founder Console in a real browser (CLAUDE.md Section 7.15), API
 * stubbed at the network boundary like e2e/review.spec.ts. The Console's own
 * rules are proven against the real database in apps/api/tests/
 * test_console_api.py; this proves the screens drive them.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const INTAKE_ID = "11111111-1111-1111-1111-111111111111";

// The Console's pages and its API share paths ("/admin/intakes/<id>"), so a
// stub must match the API's origin only -- a bare "**/admin/..." glob also
// swallows the page navigation itself.
const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function asPlatformAdmin(page: Page) {
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({ json: { email: "founder@example.test", tenant_id: null, role: null, is_platform_admin: true } }),
  );
}

test("a file chosen on an intake is uploaded as multipart and listed", async ({ page }) => {
  await asPlatformAdmin(page);
  const files: Array<{ original_filename: string }> = [];
  const uploads: Array<{ contentType: string | null; body: string }> = [];

  await page.route(`${API}/admin/intakes/${INTAKE_ID}/files`, async (route) => {
    const request = route.request();
    uploads.push({ contentType: request.headers()["content-type"] ?? null, body: request.postData() ?? "" });
    files.push({ original_filename: "acme-test-catalog.csv" });
    await route.fulfill({ status: 201, json: { file_id: "f1" } });
  });
  await page.route(`${API}/admin/intakes/${INTAKE_ID}`, (route) =>
    route.fulfill({
      json: {
        intake: {
          id: INTAKE_ID,
          prospect_name: "Acme Test Prospect",
          contact_email: null,
          received_at: "2026-09-18T10:00:00Z",
          source: "email",
          notes: null,
          linked_tenant_id: null,
          linked_tenant_name: null,
          linked_at: null,
          file_count: files.length,
          files: files.map((f, i) => ({
            id: `f${i}`,
            original_filename: f.original_filename,
            sha256: "0".repeat(64),
            byte_size: 120,
            detected_type: "csv",
            created_at: "2026-09-18T10:01:00Z",
          })),
        },
      },
    }),
  );

  await page.goto(`/admin/intakes/${INTAKE_ID}`);
  await page.getByTestId("intake-file-input").setInputFiles({
    name: "acme-test-catalog.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("sku,description\nTEST-1001,Test Beans\n"),
  });

  await expect(page.getByTestId("intake-files")).toContainText("acme-test-catalog.csv");
  expect(uploads).toHaveLength(1);
  // FormData must keep its own multipart type; a JSON Content-Type here made
  // the upload unreadable to the API.
  expect(uploads[0].contentType).toContain("multipart/form-data");
  expect(uploads[0].body).toContain("TEST-1001");
});
