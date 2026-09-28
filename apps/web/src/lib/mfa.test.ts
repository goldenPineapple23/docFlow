import { describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({ supabase: {} }));

import { uniqueFactorName } from "./mfa";

describe("naming a new authenticator (D-177)", () => {
  const day = new Date("2026-09-28T20:15:00Z");

  it("uses the plain name when nothing has it", () => {
    expect(uniqueFactorName([], day)).toBe("DocFlow Console 2026-09-28");
  });

  it("never repeats a name the account already has -- Supabase refuses that with a 422", () => {
    expect(uniqueFactorName(["DocFlow Console 2026-09-28"], day)).toBe("DocFlow Console 2026-09-28 (2)");
    expect(
      uniqueFactorName(["DocFlow Console 2026-09-28", "DocFlow Console 2026-09-28 (2)"], day),
    ).toBe("DocFlow Console 2026-09-28 (3)");
  });
});
