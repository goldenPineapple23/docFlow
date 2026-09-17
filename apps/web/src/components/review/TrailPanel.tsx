"use client";

import type { TrailEntry } from "@/lib/review";

/**
 * The audit trail (CLAUDE.md Section 7.3, and the Phase 3 exit criterion:
 * "the audit trail shows exactly what changed").
 *
 * Every edit is shown field by field, before and after, because "3 fields
 * changed" is not an audit trail. Values are rendered exactly as stored —
 * strings, at the scale they were stored at (Section 7.1).
 *
 * Rows the founder produced while acting inside the tenant are labelled
 * "DocFlow support" (Section 7.15.1), so the tenant can always tell their
 * own people's actions from ours.
 */

const ACTION_LABEL: Record<TrailEntry["action"], string> = {
  edited: "Edited",
  approved: "Approved",
  rejected: "Rejected",
  reopened: "Reopened for review",
};

export function TrailPanel({ trail }: { trail: TrailEntry[] }) {
  return (
    <section
      aria-labelledby="trail-heading"
      className="space-y-3 rounded-xl border border-gray-200 p-5"
    >
      <h2
        id="trail-heading"
        className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500"
      >
        History
      </h2>

      {trail.length === 0 ? (
        <p className="text-sm text-gray-600">Nobody has changed anything on this order yet.</p>
      ) : (
        <ol data-testid="trail" className="space-y-3">
          {trail.map((entry) => (
            <li key={entry.id} className="rounded border border-gray-200 p-3 text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-medium">{ACTION_LABEL[entry.action] ?? entry.action}</span>
                <span className="text-xs text-gray-500">
                  {entry.created_at ? new Date(entry.created_at).toLocaleString() : ""}
                </span>
              </div>

              {entry.by_docflow_support ? (
                <p data-testid="docflow-support" className="mt-0.5 text-xs text-violet-700">
                  by DocFlow support
                </p>
              ) : null}

              {entry.changes.length > 0 ? (
                <ul className="mt-2 space-y-1">
                  {entry.changes.map((change, i) => (
                    <li key={`${change.field}-${i}`} className="font-mono text-xs">
                      <span className="text-gray-600">
                        {change.line_number !== undefined ? `line ${change.line_number} · ` : ""}
                        {change.field}
                      </span>{" "}
                      <span className="text-red-700 line-through">{change.before ?? "(empty)"}</span>{" "}
                      <span aria-hidden="true">→</span>{" "}
                      <span className="text-green-800">{change.after ?? "(empty)"}</span>
                    </li>
                  ))}
                </ul>
              ) : null}

              {entry.warning_acknowledgements.length > 0 ? (
                <ul className="mt-2 space-y-1 text-xs text-gray-700">
                  {entry.warning_acknowledgements.map((ack) => (
                    <li key={ack.warning_id}>
                      Acknowledged {ack.code}: “{ack.text}”
                      {ack.note ? ` — ${ack.note}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}

              {entry.note ? <p className="mt-2 text-xs text-gray-700">{entry.note}</p> : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
