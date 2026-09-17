"use client";

import type { DocumentWarning } from "@/lib/review";

/**
 * Warnings, and the acknowledgement gate on approval (CLAUDE.md Section 7.3:
 * "any unresolved warning at approval time must be explicitly acknowledged;
 * the acknowledgement is recorded in review_actions with the warning text").
 *
 * Acknowledging is per warning and deliberately not a single "acknowledge
 * all" — the point of the gate is that a person looked at each one. The
 * checkbox label carries the warning's own text, so what the person agreed
 * to is what gets recorded.
 *
 * The prose comes from the backend, which renders it from the error catalog
 * (Section 7.16.5). This component writes no failure sentences of its own.
 */

const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, warning: 2, info: 3 };

export function WarningsPanel({
  warnings,
  acknowledged,
  disabled,
  onToggle,
}: {
  warnings: DocumentWarning[];
  acknowledged: Set<string>;
  disabled: boolean;
  onToggle: (warningId: string, checked: boolean) => void;
}) {
  const open = [...warnings]
    .filter((w) => w.status === "open")
    .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9));
  const settled = warnings.filter((w) => w.status !== "open");

  if (warnings.length === 0) {
    return (
      <section aria-labelledby="warnings-heading" className="space-y-2">
        <h2 id="warnings-heading" className="text-sm font-semibold uppercase tracking-wide text-gray-500">
          Checks
        </h2>
        <p data-testid="no-warnings" className="text-sm text-green-800">
          Everything DocFlow checks came back clean.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="warnings-heading" className="space-y-2">
      <h2 id="warnings-heading" className="text-sm font-semibold uppercase tracking-wide text-gray-500">
        Checks ({open.length} to look at)
      </h2>

      <ul className="space-y-2">
        {open.map((warning) => (
          <li
            key={warning.id}
            data-testid={`warning-${warning.code}`}
            className={[
              "rounded border p-3 text-sm",
              warning.severity === "high" || warning.severity === "critical"
                ? "border-red-300 bg-red-50"
                : "border-amber-300 bg-amber-50",
            ].join(" ")}
          >
            <div className="flex items-start gap-2">
              <input
                id={`ack-${warning.id}`}
                type="checkbox"
                disabled={disabled}
                checked={acknowledged.has(warning.id)}
                onChange={(e) => onToggle(warning.id, e.target.checked)}
                data-testid={`ack-${warning.id}`}
                className="mt-1"
              />
              <label htmlFor={`ack-${warning.id}`} className="flex-1">
                <span className="font-medium">
                  {warning.code}
                  {warning.line_number !== null ? ` · line ${warning.line_number}` : ""}
                  {warning.field_name ? ` · ${warning.field_name}` : ""}
                </span>
                <WarningDetail detail={warning.detail} />
                <span className="mt-1 block text-xs text-gray-600">
                  Tick to confirm you&apos;ve checked this against the original.
                </span>
              </label>
            </div>
          </li>
        ))}
      </ul>

      {settled.length > 0 ? (
        <p className="text-xs text-gray-500">
          {settled.length} already acknowledged or resolved.
        </p>
      ) : null}
    </section>
  );
}

/**
 * The occurrence's specifics — which two numbers disagreed, and by how much.
 * Every value is a string from the backend (Section 7.1) and is rendered as
 * text, never parsed into a Number on the way to the screen.
 */
function WarningDetail({ detail }: { detail: Record<string, string> }) {
  const entries = Object.entries(detail ?? {}).filter(([, v]) => v !== null && v !== "");
  if (entries.length === 0) return null;

  return (
    <span className="mt-1 block font-mono text-xs text-gray-700">
      {entries.map(([key, value]) => (
        <span key={key} className="mr-3 inline-block">
          {key}: {String(value)}
        </span>
      ))}
    </span>
  );
}
