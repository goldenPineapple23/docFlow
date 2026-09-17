import { describe, expect, it } from "vitest";
import { formatAmount, formatMoney } from "./money";

/**
 * Money display (CLAUDE.md Section 7.1).
 *
 * The assertions that matter here are the ones about what the formatter
 * does NOT do: it must not round, must not drop a trailing zero, and must
 * not turn a value it does not understand into something plausible-looking.
 */

describe("thousands separators", () => {
  it("groups thousands", () => {
    expect(formatAmount("1356.00")).toBe("1,356.00");
  });

  it("groups millions", () => {
    expect(formatAmount("1234567.89")).toBe("1,234,567.89");
  });

  it("leaves amounts under a thousand alone", () => {
    expect(formatAmount("570.00")).toBe("570.00");
    expect(formatAmount("999.99")).toBe("999.99");
  });

  it("handles exactly one thousand", () => {
    expect(formatAmount("1000.00")).toBe("1,000.00");
  });

  it("keeps a negative sign in front", () => {
    expect(formatAmount("-4700.50")).toBe("-4,700.50");
  });
});

describe("what it must never do", () => {
  it("never drops a trailing zero -- the scale is part of the value", () => {
    expect(formatAmount("1200.00")).toBe("1,200.00");
    expect(formatAmount("1200.50")).toBe("1,200.50");
  });

  it("never rounds, however many decimal places there are", () => {
    // A unit price is numeric(14,4); rounding it for display would make the
    // screen disagree with the document.
    expect(formatAmount("47.5000")).toBe("47.5000");
    expect(formatAmount("12345.6789")).toBe("12,345.6789");
  });

  it("never reformats a value it does not recognise", () => {
    // Extraction returns exactly what the document printed, including
    // things that are not clean decimals. Guessing here would be the same
    // mistake the model is forbidden from making.
    expect(formatAmount("1,356.00")).toBe("1,356.00");
    expect(formatAmount("approx 1500")).toBe("approx 1500");
    expect(formatAmount("$1356.00")).toBe("$1356.00");
    expect(formatAmount("")).toBe("");
  });

  it("survives null and undefined without inventing a zero", () => {
    expect(formatAmount(null)).toBe("");
    expect(formatAmount(undefined)).toBe("");
  });

  it("does not go through a float", () => {
    // 0.1 + 0.2 territory: a value with more precision than a double can
    // hold must come back with every digit it went in with.
    expect(formatAmount("9007199254740993.01")).toBe("9,007,199,254,740,993.01");
  });
});

describe("with a currency", () => {
  it("puts the code after the amount", () => {
    expect(formatMoney("1356.00", "USD")).toBe("1,356.00 USD");
  });

  it("shows a dash rather than a zero when there is no amount", () => {
    expect(formatMoney(null, "USD")).toBe("—");
    expect(formatMoney("", "USD")).toBe("—");
  });
});
