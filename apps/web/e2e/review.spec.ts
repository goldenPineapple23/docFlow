import { expect, test, type Page } from "@playwright/test";

/**
 * The review flow, end to end in a real browser (CLAUDE.md Section 6, Phase 3).
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

import { DOCUMENT_ID, WARNING_ID, detail } from "./fixtures/review";

/** Stubs the review API at the network boundary. */
async function stubApi(page: Page, state: { detail: ReturnType<typeof detail>; approved?: boolean }) {
  await page.route("**/review/documents/*/original", (route) =>
    route.fulfill({ json: { url: "/review/documents/x/original/content?token=t", expires_at: 0 } }),
  );
  await page.route("**/review/documents/*/original/content*", (route) =>
    route.fulfill({ body: "PO Number: BCH-2291", contentType: "application/octet-stream" }),
  );

  await page.route("**/review/documents/*/approve", async (route) => {
    const body = route.request().postDataJSON();
    const open = state.detail.warnings as Array<{ id: string }>;
    const acked = new Set((body.acknowledgements ?? []).map((a: { warning_id: string }) => a.warning_id));
    if (open.some((w) => !acked.has(w.id))) {
      await route.fulfill({
        status: 409,
        json: {
          detail: {
            code: "REV-001",
            title: "Some warnings still need a look",
            message: "This order has warnings nobody has acknowledged yet.",
            action: "Open each warning, fix the value or confirm it's right, then approve.",
          },
        },
      });
      return;
    }
    state.approved = true;
    state.detail.document.status = "approved";
    await route.fulfill({ json: { review_action_id: "r1", status: "approved" } });
  });

  await page.route("**/review/documents/*", async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      const body = request.postDataJSON();
      const changes: Array<Record<string, unknown>> = [];
      for (const [field, after] of Object.entries(body.header ?? {})) {
        const before = (state.detail.header as Record<string, unknown>)[field];
        if (before !== after) {
          changes.push({ field, before, after });
          (state.detail.header as Record<string, unknown>)[field] = after;
        }
      }
      state.detail.version = "version-2";
      state.detail.trail = [
        {
          id: "t1",
          action: "edited",
          user_id: "u1",
          by_docflow_support: false,
          changes,
          warning_acknowledgements: [],
          note: null,
          created_at: "2026-09-17T10:00:00Z",
        },
      ] as never;
      await route.fulfill({ json: { review_action_id: "r0", version: "version-2" } });
      return;
    }
    await route.fulfill({ json: state.detail });
  });
}

test("a reviewer corrects a field, saves, and sees exactly what changed", async ({ page }) => {
  const state = { detail: detail() };
  await stubApi(page, state);

  await page.goto(`/review/${DOCUMENT_ID}`);

  const poNumber = page.getByTestId("header-input-po_number");
  await expect(poNumber).toHaveValue("BCH-2291");

  await poNumber.fill("BCH-2292");
  await page.getByTestId("save-button").click();

  // The trail names the value before and after -- the Phase 3 exit criterion.
  const trail = page.getByTestId("trail");
  await expect(trail).toContainText("po_number");
  await expect(trail).toContainText("BCH-2291");
  await expect(trail).toContainText("BCH-2292");
});

test("money keeps its scale through an edit", async ({ page }) => {
  const state = { detail: detail() };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);

  // "570.00", not 570 -- the scale is part of the value (Section 7.1).
  await expect(page.getByTestId("header-input-order_total")).toHaveValue("570.00");
  await expect(page.getByTestId("line-1-quantity")).toHaveValue("12.0000");
});

test("the keyboard shortcut saves, and is not the only way to save", async ({ page }) => {
  const state = { detail: detail() };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);

  await page.getByTestId("header-input-po_number").fill("BCH-3000");
  await page.keyboard.press("ControlOrMeta+s");

  await expect(page.getByTestId("banner-success")).toContainText("Saved");
  // The visible button exists for anyone who does not know the shortcut.
  await expect(page.getByTestId("save-button")).toBeVisible();
});

test("escape abandons an unsaved edit without touching the document", async ({ page }) => {
  const state = { detail: detail() };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);

  const poNumber = page.getByTestId("header-input-po_number");
  await poNumber.fill("BCH-9999");
  await page.keyboard.press("Escape");

  await expect(poNumber).toHaveValue("BCH-2291");
  await expect(page.getByTestId("trail")).toHaveCount(0);
});

test("approval is blocked until every warning is ticked", async ({ page }) => {
  const state = {
    detail: detail({
      warnings: [
        {
          id: WARNING_ID,
          code: "VAL-002",
          severity: "high",
          field_name: "order_total",
          line_number: null,
          document_line_id: null,
          detail: { order_total: "100.00", sum_of_lines: "570.00" },
          status: "open",
          acknowledged_at: null,
        },
      ],
    }),
  };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);

  // Section 7.3: an unresolved warning must be explicitly acknowledged.
  await expect(page.getByTestId("approve-button")).toBeDisabled();

  await page.getByTestId(`ack-${WARNING_ID}`).check();
  await expect(page.getByTestId("approve-button")).toBeEnabled();

  await page.getByTestId("approve-button").click();
  await expect(page.getByTestId("banner-success")).toContainText("Approved");
});

test("a document carrying an embedded instruction says so before anything else", async ({ page }) => {
  const state = {
    detail: detail({
      document: { ...detail().document, injection_suspected: true },
    }),
  };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);

  // Section 7.2: forced to review with a visible banner, regardless of confidence.
  await expect(page.getByTestId("injection-banner")).toContainText("embedded instruction");
});

test("an order read with past examples says how many, and says nothing when there were none", async ({ page }) => {
  // Section 7.13 (D-141): a reviewer can see why a value may have been read
  // the way it was.
  const state = {
    detail: detail({ document: { ...detail().document, examples_used: 3 } }),
  };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);
  await expect(page.getByTestId("examples-note")).toContainText(
    "Read with 3 earlier approved orders from this customer as examples",
  );

  state.detail = detail();
  await page.reload();
  await expect(page.getByTestId("approve-button")).toBeVisible();
  await expect(page.getByTestId("examples-note")).toHaveCount(0);
});

test("the original document renders in a sandboxed frame", async ({ page }) => {
  const state = { detail: detail() };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);

  const viewer = page.getByTestId("document-viewer");
  await expect(viewer).toBeVisible();
  // Section 7.12: no scripts, no same-origin, nothing.
  await expect(viewer).toHaveAttribute("sandbox", "");
});

test("a read-only reviewer cannot edit or approve", async ({ page }) => {
  const state = { detail: detail({ can_edit: false }) };
  await stubApi(page, state);
  await page.goto(`/review/${DOCUMENT_ID}`);

  await expect(page.getByTestId("header-input-po_number")).toBeDisabled();
  await expect(page.getByTestId("approve-button")).toBeDisabled();
});

test("the queue pages through every order instead of stopping at the first 50", async ({ page }) => {
  // D-097: the queue used to show the first 50 orders and nothing past them.
  const total = 120;
  const requestedOffsets: number[] = [];
  await page.route(/\/review\/documents\?/, async (route) => {
    const url = new URL(route.request().url());
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset"));
    requestedOffsets.push(offset);
    const count = Math.max(0, Math.min(limit, total - offset));
    const documents = Array.from({ length: count }, (_, i) => ({
      ...detail().document,
      id: `00000000-0000-0000-0000-${String(offset + i).padStart(12, "0")}`,
      po_number: `TEST-${offset + i + 1}`,
      buyer_name: "Acme Test Buyer",
      order_total: "10.00",
      currency: "USD",
      open_warnings: 0,
      review_started: false,
    }));
    await route.fulfill({ json: { documents, total, limit, offset } });
  });

  await page.goto("/review");

  const range = page.getByTestId("queue-range");
  await expect(range).toHaveText("Showing 1–50 of 120");
  await expect(page.getByRole("button", { name: "← Previous" })).toBeDisabled();

  await page.getByRole("button", { name: "Next →" }).click();
  await expect(range).toHaveText("Showing 51–100 of 120");

  await page.getByRole("button", { name: "Next →" }).click();
  await expect(range).toHaveText("Showing 101–120 of 120");
  await expect(page.getByText("TEST-120", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Next →" })).toBeDisabled();

  // Changing the filter starts again from the first page.
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(range).toHaveText("Showing 1–50 of 120");
  expect(requestedOffsets).toEqual(expect.arrayContaining([0, 50, 100]));
});

// ── Export (Phase 4) ────────────────────────────────────────────────────────

function exportRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    document_id: DOCUMENT_ID,
    format: "csv",
    format_label: "CSV",
    status: "pending",
    error: null,
    sha256: null,
    byte_size: null,
    snapshot_hash: "h",
    is_current_snapshot: true,
    requested_at: "2026-09-18T10:00:00Z",
    generated_at: null,
    generated_by: "reviewer@example.test",
    by_docflow_support: false,
    ...overrides,
  };
}

async function stubExports(page: Page, finished: Record<string, unknown>) {
  const history: Array<Record<string, unknown>> = [];
  await page.route("**/review/documents/*/exports", async (route) => {
    if (route.request().method() === "POST") {
      const created = exportRecord({
        format: route.request().postDataJSON().format,
        format_label: finished.format_label,
      });
      history.unshift({ ...created, ...finished });
      await route.fulfill({ status: 202, json: { export: created } });
      return;
    }
    await route.fulfill({ json: { exports: history } });
  });
  await page.route("**/review/exports/*", async (route) => {
    const body: Record<string, unknown> = { export: exportRecord(finished) };
    if (finished.status === "ready") {
      body.download = { url: "/review/exports/x/download?token=t", expires_at: 0 };
    }
    await route.fulfill({ json: body });
  });
  await page.route("**/review/exports/*/download*", (route) =>
    route.fulfill({
      body: "po_number\r\nACME-2291\r\n",
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition": 'attachment; filename="PO-ACME-2291.csv"',
      },
    }),
  );
}

test("an approved order downloads as a file and appears in the export history", async ({ page }) => {
  const state = { detail: detail() };
  state.detail.document.status = "approved";
  await stubApi(page, state);
  await stubExports(page, { status: "ready", sha256: "abc", byte_size: 812, format_label: "CSV" });

  await page.goto(`/review/${DOCUMENT_ID}`);
  await expect(page.getByTestId("export-panel")).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("export-csv").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("PO-ACME-2291.csv");

  await expect(page.getByTestId("export-history")).toContainText("CSV");
  await expect(page.getByTestId("export-history")).toContainText("Download");
});

test("an export QuickBooks would reject says why, from the catalog", async ({ page }) => {
  const state = { detail: detail() };
  state.detail.document.status = "approved";
  await stubApi(page, state);
  await stubExports(page, {
    status: "failed",
    format_label: "QuickBooks Desktop (IIF)",
    error: {
      code: "EXP-006",
      title: "QuickBooks can't import this order yet",
      message: "QuickBooks Desktop only imports an order when its line totals add up.",
      action: "Check the totals, or download the order as CSV or Excel instead.",
    },
  });

  await page.goto(`/review/${DOCUMENT_ID}`);
  await page.getByTestId("export-iif").click();

  const error = page.getByTestId("export-error");
  await expect(error).toContainText("QuickBooks can't import this order yet");
  await expect(error).toContainText("download the order as CSV or Excel instead");
});

test("an order still in review offers no export", async ({ page }) => {
  const state = { detail: detail() };
  await stubApi(page, state);
  await stubExports(page, { status: "ready" });

  await page.goto(`/review/${DOCUMENT_ID}`);
  await expect(page.getByTestId("header-input-po_number")).toBeVisible();
  await expect(page.getByTestId("export-panel")).toHaveCount(0);
});

test("approve & export approves the order and downloads it in one click", async ({ page }) => {
  const state: { detail: ReturnType<typeof detail>; approved?: boolean } = { detail: detail() };
  await stubApi(page, state);
  await stubExports(page, { status: "ready", sha256: "abc", byte_size: 700, format_label: "JSON" });

  await page.goto(`/review/${DOCUMENT_ID}`);
  await page.getByTestId("approve-export-format").selectOption("json");

  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("approve-export-button").click();
  await downloadPromise;

  expect(state.approved).toBe(true);
  await expect(page.getByTestId("export-history")).toContainText("JSON");

  // The format choice is remembered for the next order in this browser.
  await page.reload();
  await expect(page.getByTestId("approve-export-format")).toHaveValue("json");
});

test("approve & export is blocked by unticked warnings, like approve", async ({ page }) => {
  const state: { detail: ReturnType<typeof detail>; approved?: boolean } = {
    detail: detail({
      warnings: [
        {
          id: WARNING_ID,
          code: "VAL-002",
          title: "The order total doesn't match the lines",
          message: "m",
          action: "a",
          severity: "high",
          field_name: "order_total",
          line_number: null,
          document_line_id: null,
          detail: {},
          status: "open",
          acknowledged_at: null,
        },
      ],
    }),
  };
  await stubApi(page, state);
  await stubExports(page, { status: "ready" });

  await page.goto(`/review/${DOCUMENT_ID}`);
  await expect(page.getByTestId("approve-export-button")).toBeDisabled();
  expect(state.approved).toBeUndefined();
});
