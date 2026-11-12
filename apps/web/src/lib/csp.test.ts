import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ADMIN_HEADERS, SECURITY_HEADERS, buildCsp, newNonce } from "./csp";
import { PUBLIC_ENV_NAMES, deployedBuildProblems, originOf } from "./origins";

const API = "https://docflow-api-staging.fly.dev";
const SUPABASE = "https://project-ref.supabase.co";

function directives(csp: string): Map<string, string[]> {
  return new Map(
    csp.split("; ").map((part) => {
      const [name, ...sources] = part.split(" ");
      return [name, sources];
    }),
  );
}

describe("the CSP (D-237)", () => {
  const production = buildCsp({ nonce: "n0nce", apiOrigin: API, supabaseOrigin: SUPABASE, development: false });

  it("is exactly the policy the design lists, in production", () => {
    expect(production).toBe(
      [
        "default-src 'self'",
        "script-src 'self' 'nonce-n0nce' 'strict-dynamic'",
        "style-src 'self' 'nonce-n0nce'",
        `img-src 'self' data: ${API}`,
        "font-src 'self'",
        `connect-src 'self' ${API} ${SUPABASE}`,
        `frame-src ${API}`,
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
      ].join("; "),
    );
  });

  it("allows no inline script, no eval and no wildcard in production", () => {
    expect(production).not.toContain("'unsafe-inline'");
    expect(production).not.toContain("'unsafe-eval'");
    expect(production).not.toContain("*");
    expect(production).not.toContain("storage.supabase.co");
  });

  it("adds eval, inline styles and hot reload only in development", () => {
    const dev = directives(buildCsp({ nonce: "n", apiOrigin: API, supabaseOrigin: SUPABASE, development: true }));
    expect(dev.get("script-src")).toContain("'unsafe-eval'");
    expect(dev.get("script-src")).toContain("'nonce-n'");
    expect(dev.get("style-src")).toEqual(["'self'", "'unsafe-inline'"]);
    expect(dev.get("connect-src")).toContain("ws:");
  });

  it("lists a source once even when two origins are the same", () => {
    const same = directives(buildCsp({ nonce: "n", apiOrigin: API, supabaseOrigin: API, development: false }));
    expect(same.get("connect-src")).toEqual(["'self'", API]);
  });

  it("gets a fresh 128-bit nonce on every call", () => {
    const nonces = new Set(Array.from({ length: 50 }, () => newNonce()));
    expect(nonces.size).toBe(50);
    for (const nonce of nonces) expect(atob(nonce)).toHaveLength(16);
  });

  it("carries the other headers the design lists, and noindex for the Console", () => {
    expect(Object.fromEntries(SECURITY_HEADERS.map((h) => [h.key, h.value]))).toEqual({
      "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "X-Frame-Options": "DENY",
      "Permissions-Policy":
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), hid=()",
    });
    expect(ADMIN_HEADERS).toEqual([{ key: "X-Robots-Tag", value: "noindex, nofollow" }]);
  });
});

describe("the origins the CSP and the app share (D-237)", () => {
  it("reduces a URL to its origin", () => {
    expect(originOf("https://docflow-api-staging.fly.dev/")).toBe(API);
    expect(originOf("http://localhost:8000")).toBe("http://localhost:8000");
    expect(originOf(`${SUPABASE}/auth/v1`)).toBe(SUPABASE);
  });

  it("exposes exactly three public names to the browser", () => {
    expect([...PUBLIC_ENV_NAMES]).toEqual([
      "NEXT_PUBLIC_API_BASE_URL",
      "NEXT_PUBLIC_SUPABASE_URL",
      "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    ]);
  });

  const good = {
    VERCEL_ENV: "production",
    NEXT_PUBLIC_API_BASE_URL: API,
    NEXT_PUBLIC_SUPABASE_URL: SUPABASE,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon",
  };

  it("lets a Vercel build with https URLs and a key go out", () => {
    expect(deployedBuildProblems(good)).toEqual([]);
  });

  it("checks nothing off Vercel, so local and CI builds keep their fallbacks", () => {
    expect(deployedBuildProblems({})).toEqual([]);
  });

  it.each([
    [{ NEXT_PUBLIC_API_BASE_URL: undefined }, "NEXT_PUBLIC_API_BASE_URL is not set"],
    [{ NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000" }, "NEXT_PUBLIC_API_BASE_URL is not https"],
    [{ NEXT_PUBLIC_SUPABASE_URL: `${SUPABASE}/rest` }, "NEXT_PUBLIC_SUPABASE_URL has a path"],
    [{ NEXT_PUBLIC_SUPABASE_URL: "not a url" }, "NEXT_PUBLIC_SUPABASE_URL is not a URL"],
    [{ NEXT_PUBLIC_SUPABASE_ANON_KEY: "" }, "NEXT_PUBLIC_SUPABASE_ANON_KEY is not set"],
  ])("stops a Vercel build that would ship %o", (change, problem) => {
    expect(deployedBuildProblems({ ...good, ...change })).toContain(problem);
  });
});

describe("vercel.json (D-237; RUNBOOK 13)", () => {
  const config = JSON.parse(readFileSync(path.resolve(__dirname, "../../vercel.json"), "utf8"));

  it("builds as CI's web job does, next to the API's region, and never deploys on a push", () => {
    expect(config).toEqual({
      $schema: "https://openapi.vercel.sh/vercel.json",
      framework: "nextjs",
      installCommand: "npm ci",
      buildCommand: "npm run build",
      regions: ["iad1"],
      git: { deploymentEnabled: false },
    });
  });
});
