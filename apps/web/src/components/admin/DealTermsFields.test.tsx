import { describe, expect, it } from "vitest";
import { dealSummary, money, presetLabel } from "./DealTermsFields";
import type { SetupFeePreset } from "@/lib/admin";

const preset = (over: Partial<SetupFeePreset>): SetupFeePreset => ({
  id: "p",
  code: "standard",
  version: 1,
  name: "Standard",
  description: "",
  default_amount: "1500.00",
  min_amount: "1500.00",
  max_amount: "1500.00",
  note_required: false,
  ...over,
});

describe("deal terms display (D-117)", () => {
  it("formats money strings without float arithmetic", () => {
    expect(money("1500.00")).toBe("$1,500");
    expect(money("2350.50")).toBe("$2,350.50");
    expect(money("0.00")).toBe("$0");
    expect(money(null)).toBe("—");
  });

  it("labels fixed, ranged and custom presets", () => {
    expect(presetLabel(preset({}))).toBe("Standard — $1,500");
    expect(
      presetLabel(preset({ name: "Complex", default_amount: "2000.00", min_amount: "2000.00", max_amount: "2500.00" })),
    ).toBe("Complex — $2,000–$2,500");
    expect(presetLabel(preset({ name: "Custom", default_amount: null, min_amount: "0.00", max_amount: null }))).toBe(
      "Custom",
    );
  });

  it("summarises a founding deal and a waived one in one line", () => {
    const base = {
      tierName: "Growth",
      monthlyPrice: "399.00",
      promoMonthlyPrice: "249.00",
      promoDays: 90,
      feeBilling: "stripe",
      note: null,
    };
    expect(
      dealSummary({ ...base, founding: true, feeAmount: "750.00", presetName: "Founding customer" }),
    ).toBe("Growth at $249/month for 3 months (founding), then $399 · $750 founding customer setup fee on the first invoice");
    expect(
      dealSummary({ ...base, founding: false, feeAmount: "0.00", presetName: "Waived", note: "Test pilot" }),
    ).toBe("Growth at $399/month · setup fee waived (Test pilot)");
  });
});
