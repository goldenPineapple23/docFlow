import { readFileSync } from "node:fs";
import { expect, test } from "./cspGuarded";
import { SEED_FILE } from "../playwright.live.config";
import { openOrder, signIn } from "./session";

/**
 * Page images load across sites (D-228 item 9; D-233).
 *
 * Run by the `cross-site` project only: the web app on `localhost`, the API
 * on `127.0.0.1`. Those are different sites, not only different origins, as
 * the web app and the API will be once deployed. The image needs no CORS (an
 * `<img>` without `crossorigin` never asks), and loads because the page route
 * sends `Cross-Origin-Resource-Policy: cross-origin`; `same-site` would block
 * it here. The web app sends no CSP and no COEP today (Phase 6).
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

const seed: {
  email: string;
  password: string;
  viewer_documents: Record<string, { document_id: string; pages: number }>;
} = JSON.parse(readFileSync(SEED_FILE, "utf8"));

test("every page of a multi-page TIFF loads with the web app and the API on different sites", async ({ page }) => {
  const tiff = seed.viewer_documents["po.tif"];
  const served: { url: string; status: number; corp: string | undefined }[] = [];
  page.on("response", (response) => {
    if (/\/pages\/\d+\?token=/.test(response.url())) {
      served.push({
        url: response.url(),
        status: response.status(),
        corp: response.headers()["cross-origin-resource-policy"],
      });
    }
  });
  await signIn(page, seed);
  await openOrder(page, tiff.document_id);
  expect(new URL(page.url()).hostname).toBe("localhost");

  const image = page.getByTestId("page-image");
  for (let index = 0; index < tiff.pages; index++) {
    await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBeGreaterThan(0);
    if (index < tiff.pages - 1) await page.getByTestId("page-next").click();
  }

  expect(served).toHaveLength(tiff.pages);
  for (const response of served) {
    expect(new URL(response.url).hostname).toBe("127.0.0.1");
    expect(response.status).toBe(200);
    expect(response.corp).toBe("cross-origin");
  }
  await expect(page.getByTestId("page-unavailable")).toHaveCount(0);
});
