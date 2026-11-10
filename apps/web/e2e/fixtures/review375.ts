import { deflateSync } from "node:zlib";
import { type Page } from "@playwright/test";
import { DOCUMENT_ID, LINE_ID, WARNING_ID, detail } from "./review";

/**
 * The review screen at phone width (A5; D-234): the stubs both 375 px specs
 * share. The API is stubbed at the network boundary, as in review.spec.ts.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

export const PHONE = { width: 375, height: 812 };

export const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type View = "pages" | "text" | "frame" | "panel";
export const VIEWS: View[] = ["pages", "text", "frame", "panel"];

/** Who `/auth/me` says is signed in: an owner (every header link) or nobody (the request fails). */
export type Who = "owner" | "unanswered";

/** A grey page with a black line every 20 pixels, so a point on it can be found again after a zoom. */
export const PAGE_WIDTH = 400;
export const PAGE_HEIGHT = 520;
export const GRID_PAGE = gridPng(PAGE_WIDTH, PAGE_HEIGHT);

function gridPng(width: number, height: number): Buffer {
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(width + 1);
    for (let x = 0; x < width; x++) row[x + 1] = x % 20 === 0 || y % 20 === 0 ? 0 : 0xdd;
    rows.push(row);
  }
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 0; // greyscale
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let k = 0; k < 8; k++) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

const DOC_032 = {
  code: "DOC-032",
  title: "This page can't be shown",
  message: "DocFlow couldn't show this page here. What DocFlow read from the document isn't affected.",
  action: "Download the original to see this page.",
};

const LINKS = {
  url: "/review/documents/x/original/content?token=t",
  download_url: "/review/documents/x/original/content?token=t&download=1",
  expires_at: 0,
  preview_kind: null,
};

function original(view: View) {
  switch (view) {
    case "pages":
      return {
        ...LINKS,
        view,
        pages: [0, 1].map((index) => ({
          index,
          drawn: true,
          width: PAGE_WIDTH,
          height: PAGE_HEIGHT,
          url: `/review/documents/x/pages/${index}?token=p`,
        })),
        page_unavailable: DOC_032,
        previewable: true,
        format: "a PDF",
        filename: "po.pdf",
      };
    case "text":
      return { ...LINKS, view, previewable: true, format: "a text file", filename: "po.txt" };
    case "frame":
      return { ...LINKS, view, previewable: true, format: "a PNG image", filename: "po.png" };
    case "panel":
      return {
        ...LINKS,
        view,
        previewable: false,
        format: "a PDF",
        filename: "po.pdf",
        unavailable: null,
        not_shown: {
          code: "DOC-031",
          title: "This PDF can't be shown here",
          message: "DocFlow read this PDF, but it can't show the pages in this panel.",
          action: "Download the original if you need to check a value against it.",
        },
      };
  }
}

/** 400 lines of stored text: long enough to push everything below it off the page if it isn't boxed. */
export const LONG_TEXT = Array.from(
  { length: 400 },
  (_, i) => `Line ${i + 1}: PO BCH-2291  CF-1001  Colombian Whole Bean 5lb  12 CS  47.50  570.00`,
).join("\n");

/** An order with three lines, candidates on each, and one open check on line 1. */
export function phoneDetail() {
  const base = detail();
  const lines = [1, 2, 3].map((n) => ({
    ...base.lines[0],
    id: n === 1 ? LINE_ID : `bbbbbbbb-bbbb-bbbb-bbbb-00000000000${n}`,
    line_number: n,
    sku: `CF-100${n}`,
    match_candidates: [{ item_id: `i${n}`, sku: `CF-100${n}-X`, description: "Colombian WB 5 lb case", score: "0.71" }],
  }));
  return detail({
    lines,
    warnings: [
      {
        id: WARNING_ID,
        code: "VAL-001",
        title: "Line total doesn't match quantity × unit price",
        message: "12.0000 × 47.5000 is 570.00; the document says 571.00.",
        action: "Check the line against the original.",
        severity: "warning",
        field_name: "line_total",
        line_number: 1,
        document_line_id: LINE_ID,
        detail: { quantity: "12.0000", unit_price: "47.5000", line_total: "571.00" },
        status: "open",
        acknowledged_at: null,
      },
    ],
  });
}

/**
 * Stubs the tenant review API for one view. `prefix` is the review API's
 * prefix: `/review` for a tenant user, the acting-as prefix for the Console.
 */
export async function stubReview(
  page: Page,
  { view = "pages", who = "owner", prefix = "/review" }: { view?: View; who?: Who; prefix?: string } = {},
) {
  const state = { detail: phoneDetail(), approved: false };
  const base = `${API}${prefix}/documents/${DOCUMENT_ID}`;

  if (who === "owner") {
    await page.route(`${API}/auth/me`, (route) =>
      route.fulfill({
        json: {
          email: "reviewer@example.test",
          tenant_name: "Acme Test Distributor of Industrial Supplies",
          role: "owner",
        },
      }),
    );
  } else {
    await page.route(`${API}/auth/me`, (route) => route.abort("failed"));
  }
  await page.route(`${base}/original`, (route) => route.fulfill({ json: original(view) }));
  await page.route(`${base}/text`, (route) =>
    route.fulfill({ json: { text: LONG_TEXT, truncated: false, cut_at: 200000 } }),
  );
  await page.route(`${base}/exports`, (route) => route.fulfill({ json: { exports: [] } }));
  await page.route(`${API}/review/documents/x/original/content*`, (route) =>
    route.fulfill({ body: GRID_PAGE, contentType: "image/png" }),
  );
  await page.route(`${API}/review/documents/x/pages/*`, (route) =>
    route.fulfill({ body: GRID_PAGE, contentType: "image/png" }),
  );
  await page.route(`${base}/approve`, async (route) => {
    state.approved = true;
    state.detail.document.status = "approved";
    await route.fulfill({ json: { review_action_id: "r1", status: "approved" } });
  });
  await page.route(base, (route) => route.fulfill({ json: state.detail }));
  return state;
}

/** Opens the review screen and waits for the view to be on screen. */
export async function openReview(page: Page, view: View, path = `/review/${DOCUMENT_ID}`) {
  await page.goto(path);
  await page.getByTestId("approve-button").waitFor();
  if (view === "pages") {
    await page.waitForFunction(
      () => (document.querySelector("[data-testid=page-image]") as HTMLImageElement | null)?.naturalWidth === 400,
    );
  } else if (view === "text") {
    await page.getByTestId("document-text").waitFor();
  } else if (view === "frame") {
    await page.getByTestId("document-viewer").waitFor();
  } else {
    await page.getByTestId("viewer-not-previewable").waitFor();
  }
}

/** The element each view draws the document in. */
export const VIEW_ELEMENT: Record<View, string> = {
  pages: "page-viewer",
  text: "document-text",
  frame: "document-viewer",
  panel: "viewer-not-previewable",
};

/** How far the page scrolls sideways, in CSS pixels; 0 or less means not at all. */
export function sideways(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** The page pixel (in the image's own pixels) under a point on the screen. */
export function pagePixelAt(page: Page, x: number, y: number): Promise<{ px: number; py: number }> {
  return page.evaluate(
    ([sx, sy]) => {
      const image = document.querySelector("[data-testid=page-image]") as HTMLImageElement;
      const box = image.getBoundingClientRect();
      return {
        px: ((sx - box.left) / box.width) * image.naturalWidth,
        py: ((sy - box.top) / box.height) * image.naturalHeight,
      };
    },
    [x, y],
  );
}
