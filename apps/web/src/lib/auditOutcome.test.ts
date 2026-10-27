import { describe, expect, it } from "vitest";
import { UNKNOWN_OUTCOME, outcomeText } from "./auditOutcome";

describe("the Audit tab's outcome (D-178)", () => {
  it("reads a succeeded action as done", () => {
    expect(outcomeText("succeeded", { result: "succeeded", code: null, at: "2026-10-27T00:00:00Z" })).toEqual({
      text: "Done",
      tone: "ok",
    });
  });

  it("names the catalog code of a failed action", () => {
    expect(outcomeText("failed", { result: "failed", code: "BIL-005", at: "2026-10-27T00:00:00Z" })).toEqual({
      text: "Failed: BIL-005",
      tone: "failed",
    });
  });

  it("reads a failure with no catalog code as failed, not done", () => {
    expect(outcomeText("failed", { result: "failed", code: null, at: "2026-10-27T00:00:00Z" })?.text).toBe("Failed");
  });

  it("never reads an action with no recorded outcome as done", () => {
    const shown = outcomeText("unknown", null);
    expect(shown).toEqual({ text: UNKNOWN_OUTCOME, tone: "unknown" });
    expect(shown?.text).toBe(
      "Outcome unknown: no outcome was recorded. Check the tenant's current state before retrying.",
    );
  });

  it("marks an action from before outcomes were kept", () => {
    expect(outcomeText("not_recorded", null)?.text).toBe("Outcome not recorded (before D-178)");
  });

  it("shows nothing for an account event or a view", () => {
    expect(outcomeText(null, null)).toBeNull();
    expect(outcomeText(undefined, undefined)).toBeNull();
  });
});
