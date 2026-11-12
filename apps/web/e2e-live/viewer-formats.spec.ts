import { readFileSync } from "node:fs";
import { type Page } from "@playwright/test";
import { expect, test } from "./cspGuarded";
import { SEED_FILE } from "../playwright.live.config";
import { openOrder, signIn } from "./session";

/**
 * The viewer, for a real PO in every format a distributor's buyers send
 * (CLAUDE.md Section 7.11's positive fixtures; D-233).
 *
 * Nothing is stubbed: the dev parse service read and drew each one, the seed
 * stored what it answered the way the worker does, and the real API serves
 * it. A drawn format shows every page in the page, with zoom; a text format
 * shows the text DocFlow stored, in the page, as characters. Neither ever
 * reaches the frame (D-228 item 1's answer).
 *
 * The parse service's own suite proves each format is read correctly; this
 * proves the API and the screen show what it read.
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

type Seeded = { document_id: string; pages: number; text_starts: string | null };

const seed: { email: string; password: string; viewer_documents: Record<string, Seeded> } = JSON.parse(
  readFileSync(SEED_FILE, "utf8"),
);

test.use({ viewport: { width: 1440, height: 900 } });

const DRAWN = ["po.pdf", "po-scanned.pdf", "po.tif", "po.heic", "po.jpg", "po.png", "po.gif", "po.webp"];
// `po.doc` isn't here: it needs LibreOffice, which the web-live job doesn't
// have, and its text shows by the same path as `po.docx` (D-233 item 8).
const TEXT = [
  "po.txt",
  "po.csv",
  "po.md",
  "po.docx",
  "po.xlsx",
  "po.xlsm",
  "po.xls",
  "po.odt",
  "po.ods",
  "po.rtf",
  "po.eml",
  "po.msg",
  "po.html",
];

test("every document the seed made has a test here, and no test lacks its document", () => {
  // A new positive fixture is seeded automatically; this fails until it is
  // named in DRAWN or TEXT, so it can't reach web-live without a test.
  expect(Object.keys(seed.viewer_documents).sort()).toEqual([...DRAWN, ...TEXT, "markup.txt"].sort());
});

async function imageLoaded(page: Page) {
  const image = page.getByTestId("page-image");
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBeGreaterThan(0);
  return image;
}

for (const name of DRAWN) {
  test(`${name}: every page shows in the page, never the frame, and zooms`, async ({ page }) => {
    const seeded = seed.viewer_documents[name];
    expect(seeded.pages).toBeGreaterThan(0);
    await signIn(page, seed);
    await openOrder(page, seeded.document_id);

    for (let index = 0; index < seeded.pages; index++) {
      const label = `Page ${index + 1} of ${seeded.pages}`;
      await expect(page.getByTestId("page-position")).toHaveText(label);
      const image = await imageLoaded(page);
      await expect(image).toHaveAttribute("alt", label);

      const fitted = (await image.boundingBox())!.width;
      await page.getByRole("button", { name: "Zoom in" }).click();
      await expect.poll(async () => (await image.boundingBox())!.width).toBeGreaterThan(fitted);
      await page.getByRole("button", { name: "Fit to width" }).click();
      await expect.poll(async () => Math.round((await image.boundingBox())!.width)).toBe(Math.round(fitted));

      if (index < seeded.pages - 1) await page.getByTestId("page-next").click();
    }
    await expect(page.getByTestId("page-unavailable")).toHaveCount(0);
    await expect(page.getByTestId("document-viewer")).toHaveCount(0);
  });
}

for (const name of TEXT) {
  test(`${name}: the text DocFlow stored shows in the page, never the frame`, async ({ page }) => {
    const seeded = seed.viewer_documents[name];
    expect(seeded.text_starts).toBeTruthy();
    await signIn(page, seed);
    await openOrder(page, seeded.document_id);

    await expect(page.getByTestId("document-text")).toContainText(seeded.text_starts!);
    await expect(page.getByTestId("preview-kind")).toContainText("Text DocFlow read from");
    await expect(page.getByTestId("document-viewer")).toHaveCount(0);
  });
}

test("markup in a text file shows as the characters it is, never as HTML", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });
  await signIn(page, seed);
  await openOrder(page, seed.viewer_documents["markup.txt"].document_id);

  const shown = page.getByTestId("document-text");
  await expect(shown).toContainText('<img src="x" onerror="window.__ran = true">');
  await expect(shown).toContainText("<script>window.__ran = true</script><b>bold</b>");
  await expect(shown.locator("img, script, b")).toHaveCount(0);
  expect(await page.evaluate(() => (window as { __ran?: boolean }).__ran)).toBeUndefined();
  expect(dialogs).toEqual([]);
});
