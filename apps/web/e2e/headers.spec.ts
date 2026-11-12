import { DOCUMENT_ID } from "./fixtures/review";
import { expect, test } from "./networkGuard";

/**
 * The web app's security headers and CSP as the production build serves them
 * (CLAUDE.md 7.12; D-235 item 2; D-237). Run in Chromium and in WebKit.
 * Every title starts "headers:" so the WebKit project can select them.
 */

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const PAGES = ["/", "/login", "/admin", "/admin/tenants", `/review/${DOCUMENT_ID}`];

const EXPECTED = {
  "strict-transport-security": "max-age=63072000; includeSubDomains",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
  "x-frame-options": "DENY",
  "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), hid=()",
};

function nonceOf(csp: string): string {
  const match = csp.match(/'nonce-([^']+)'/);
  expect(match, `no nonce in ${csp}`).not.toBeNull();
  return match![1];
}

for (const path of PAGES) {
  test(`headers: ${path} carries every security header and a CSP with its own nonce`, async ({ request }) => {
    const response = await request.get(path);
    expect(response.status()).toBe(200);
    const headers = response.headers();
    for (const [name, value] of Object.entries(EXPECTED)) expect(headers[name], name).toBe(value);

    const csp = headers["content-security-policy"];
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain(`img-src 'self' data: ${new URL(API).origin}`);
    expect(csp).toContain(`frame-src ${new URL(API).origin}`);
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain("'unsafe-inline'");
    expect(csp).not.toContain("'unsafe-eval'");

    // Every script Next wrote into the page carries this response's nonce.
    const nonce = nonceOf(csp);
    const html = await response.text();
    const scripts = [...html.matchAll(/<script\b[^>]*>/g)].map((m) => m[0]);
    expect(scripts.length).toBeGreaterThan(0);
    for (const tag of scripts) expect(tag, tag).toContain(`nonce="${nonce}"`);

    const robots = headers["x-robots-tag"];
    if (path.startsWith("/admin")) expect(robots).toBe("noindex, nofollow");
    else expect(robots).toBeUndefined();
  });
}

test("headers: the nonce is new on every request, so no page is served from a build-time copy", async ({ request }) => {
  const nonces = new Set<string>();
  for (let i = 0; i < 3; i += 1) {
    nonces.add(nonceOf((await request.get("/login")).headers()["content-security-policy"]));
  }
  expect(nonces.size).toBe(3);
});

test("headers: a built file carries the headers but no CSP of its own", async ({ request }) => {
  const html = await (await request.get("/login")).text();
  const asset = html.match(/\/_next\/static\/[^"']+\.js/)?.[0];
  expect(asset, "no /_next/static script in the page").toBeTruthy();
  const headers = (await request.get(asset!)).headers();
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["strict-transport-security"]).toBe(EXPECTED["strict-transport-security"]);
  expect(headers["content-security-policy"]).toBeUndefined();
});

test("headers: the guard sees a violation, so a quiet run means none happened", async ({ page, cspViolations }) => {
  await page.goto("/login");
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  expect(cspViolations).toEqual([]);

  // An image from a host the policy doesn't name: the browser refuses it
  // before it leaves (so the network guard never sees it), and says so.
  await page.evaluate(() => {
    const img = document.createElement("img");
    img.src = "https://not-allowed.example.test/x.png";
    document.body.append(img);
  });
  await expect.poll(() => cspViolations.length).toBeGreaterThan(0);
  expect(cspViolations.join("\n")).toMatch(/img-src|not-allowed\.example\.test/);
  cspViolations.length = 0;
});
