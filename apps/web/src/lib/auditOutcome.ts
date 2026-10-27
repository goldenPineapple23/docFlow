/**
 * How the Audit tab reads a Console action's outcome (D-178, D-220). The API
 * attaches each `outcome` row to the action it belongs to, and says which of
 * these states the line is in; this decides the words.
 *
 * "unknown" is a write whose outcome was never recorded -- a request that
 * stopped midway. It must never read as done (the founder's wording,
 * 2026-10-23).
 */

export type OutcomeState = "succeeded" | "failed" | "unknown" | "not_recorded" | null;

export type AuditOutcome = { result: string | null; code: string | null; at: string } | null;

export type OutcomeText = { text: string; tone: "ok" | "failed" | "unknown" | "muted" };

export const UNKNOWN_OUTCOME =
  "Outcome unknown: no outcome was recorded. Check the tenant's current state before retrying.";

export function outcomeText(state: OutcomeState | undefined, outcome: AuditOutcome | undefined): OutcomeText | null {
  switch (state) {
    case "succeeded":
      return { text: "Done", tone: "ok" };
    case "failed":
      return { text: outcome?.code ? `Failed: ${outcome.code}` : "Failed", tone: "failed" };
    case "unknown":
      return { text: UNKNOWN_OUTCOME, tone: "unknown" };
    case "not_recorded":
      return { text: "Outcome not recorded (before D-178)", tone: "muted" };
    default:
      return null;
  }
}
