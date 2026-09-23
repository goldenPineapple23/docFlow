import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Where the allowance and held-document notices may appear (D-126): on the
 * customer's Purchase orders list only -- never in the shared review chrome, and
 * so never while a person is reviewing an order. Enforced by reading the
 * source, like the other surface rules, because the failure is easy to
 * reintroduce and silent (a notice quietly appearing over every order).
 */
const SRC = path.resolve(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8");

describe("the notices belong to the queue page only", () => {
  it("is on the Purchase orders list", () => {
    expect(read("app/review/page.tsx")).toContain("<HeldStrip");
  });

  it("is not in the review chrome shared with every order", () => {
    expect(read("components/review/ReviewChrome.tsx")).not.toContain("HeldStrip");
  });

  it("is not on the single-order review screen", () => {
    expect(read("app/review/[id]/page.tsx")).not.toContain("HeldStrip");
  });

  it("is not shown when the founder is acting as a tenant", () => {
    expect(read("app/review/page.tsx")).toMatch(/!scope\.tenantId\s*\?\s*<HeldStrip/);
  });
});
