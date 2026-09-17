/**
 * Per-field confidence indicators (CLAUDE.md Section 7.1: "Every field gets a
 * confidence score. Anything below threshold is visibly flagged in review").
 *
 * The threshold is 0.80 (Section 3). "Visibly flagged" is taken literally:
 * a low-confidence field gets a colour, a border AND a text label, because
 * colour alone is invisible to a reviewer who cannot distinguish it and
 * unreadable in a printout.
 */

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
      title={low ? "Below the 80% confidence threshold — check this against the document" : undefined}
      className={
        low
          ? "rounded px-1.5 py-0.5 text-xs font-medium bg-amber-100 text-amber-900 ring-1 ring-amber-400"
          : "rounded px-1.5 py-0.5 text-xs text-gray-500"
      }
    >
      {low ? `Low confidence · ${percent}` : percent}
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
