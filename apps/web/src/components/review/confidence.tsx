/**
 * Per-field confidence indicators (CLAUDE.md Section 7.1: "Every field gets a
 * confidence score. Anything below threshold is visibly flagged in review").
 *
 * The threshold is 0.80 (Section 3). "Visibly flagged" is taken literally:
 * a low-confidence field gets a colour, a border AND a text label, because
 * colour alone is invisible to a reviewer who cannot distinguish it and
 * unreadable in a printout.
 */

import { PILL } from "@/components/StatusBadge";

export const CONFIDENCE_THRESHOLD = 0.8;

export function isLowConfidence(value: string | number | null | undefined): boolean {
  if (value === null || value === undefined || value === "") return false;
  const parsed = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(parsed)) return false;
  return parsed < CONFIDENCE_THRESHOLD;
}

export function confidencePercent(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(parsed)) return null;
  return `${Math.round(parsed * 100)}%`;
}

export function ConfidenceBadge({ value }: { value: string | number | null | undefined }) {
  const percent = confidencePercent(value);
  if (percent === null) return null;

  const low = isLowConfidence(value);
  return (
    <span
      data-testid="confidence-badge"
      data-low={low ? "true" : "false"}
      // The visible label is short because it sits in a narrow column, but
      // the meaning must not depend on reading a tooltip or seeing a colour
      // (Section 7.1: "visibly flagged"). Assistive tech gets the sentence.
      aria-label={
        low
          ? `Low confidence, ${percent}. Below the 80% threshold — check this against the document.`
          : `Confidence ${percent}.`
      }
      title={
        low
          ? `Low confidence (${percent}) — below the 80% threshold. Check this against the document.`
          : `DocFlow was ${percent} confident reading this.`
      }
      className={
        low
          ? `${PILL} bg-amber-100 text-amber-800`
          : "whitespace-nowrap text-xs tabular-nums text-gray-500"
      }
    >
      {low ? `Low · ${percent}` : percent}
    </span>
  );
}

/**
 * Why a value is what it is (Section 7.13: "Every learned rule that fires on
 * a document is recorded as provenance on the affected field, so a reviewer
 * can see why a value was pre-filled").
 */
export function ProvenanceNote({ provenance }: { provenance: string | undefined }) {
  if (!provenance) return null;

  if (provenance.startsWith("human_edit")) {
    return <span className="text-xs text-blue-700">Edited by a person</span>;
  }
  if (provenance.startsWith("learned_rule")) {
    return <span className="text-xs text-violet-700">Filled in by a saved rule</span>;
  }
  if (provenance.startsWith("mapped:")) {
    return <span className="text-xs text-gray-500">Matched to the catalog</span>;
  }
  return null;
}
