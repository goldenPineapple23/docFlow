"use client";

import type { DocumentWarning } from "@/lib/review";
import { HEADER_FIELDS } from "./HeaderFields";

// Field names as the reviewer sees them on screen, so a check says
// "Currency", not `currency` (the first founder test couldn't tell which of
// eight empty boxes a check meant).
const LINE_FIELD_LABELS: Record<string, string> = {
  sku: "SKU",
  description: "Description",
  quantity: "Qty",
  unit: "Unit",
  unit_price: "Unit price",
  line_total: "Line total",
};

function fieldLabel(warning: DocumentWarning): string | null {
  if (!warning.field_name) return null;
  if (warning.line_number !== null) return LINE_FIELD_LABELS[warning.field_name] ?? null;
  return HEADER_FIELDS.find((f) => f.name === warning.field_name)?.label ?? null;
}

/** Scroll to the box a check is about and put the cursor in it. */
function goTo(warning: DocumentWarning) {
  const target =
    warning.line_number !== null
      ? document.querySelector<HTMLElement>(
          warning.field_name
            ? `[data-testid="line-${warning.line_number}-${warning.field_name}"]`
            : `[data-testid="line-${warning.line_number}"]`,
        )
      : warning.field_name
        ? document.getElementById(`header-${warning.field_name}`)
        : null;
  if (!target) return;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.focus({ preventScroll: true });
}

// Keys already said in the check's heading.
const DETAIL_SHOWN_ELSEWHERE = new Set(["field", "scope"]);

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
      <section
        aria-labelledby="warnings-heading"
        className="space-y-3 rounded-xl border border-amber-400 bg-amber-100/80 p-5"
      >
        <h2 id="warnings-heading" className="text-[11px] font-semibold uppercase tracking-[0.08em] text-amber-900">
          Checks
        </h2>
        <p data-testid="no-warnings" className="text-sm text-green-800">
          Everything DocFlow checks came back clean.
        </p>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="warnings-heading"
      className="space-y-3 rounded-xl border border-amber-400 bg-amber-100/80 p-5"
    >
      <div>
        <h2 id="warnings-heading" className="text-[11px] font-semibold uppercase tracking-[0.08em] text-amber-900">
          Checks — {open.length} to tick
        </h2>
        {/*
          Ticking a check is not an edit and is not saved on its own; it is
          how you tell DocFlow you have looked at something before approving.
          The first walkthrough tester ticked one, pressed Save, and nothing
          happened -- because Save is for changed values, and nothing said so.
        */}
        <p className="text-xs text-amber-900/80">
          These don&apos;t need saving. Tick each one to confirm you&apos;ve looked at it — that&apos;s
          what unlocks Approve.
        </p>
      </div>

      <ul className="space-y-2">
        {open.map((warning) => (
          <li
            key={warning.id}
            data-testid={`warning-${warning.code}`}
            className={[
              "rounded-lg border bg-white p-4 text-sm",
              warning.severity === "high" || warning.severity === "critical"
                ? "border-red-200"
                : "border-amber-200",
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
              <label htmlFor={`ack-${warning.id}`} className="flex-1 cursor-pointer">
                <span className="block font-medium text-gray-900">
                  {warning.line_number !== null ? `Line ${warning.line_number} · ` : ""}
                  {fieldLabel(warning) ? (
                    <span data-testid={`warning-field-${warning.id}`} className="text-amber-900">
                      {fieldLabel(warning)}:{" "}
                    </span>
                  ) : null}
                  {warning.title ?? warning.code}
                </span>
                {warning.message ? (
                  <span className="mt-0.5 block text-sm text-gray-700">{warning.message}</span>
                ) : null}
                {warning.action ? (
                  <span className="mt-0.5 block text-sm text-gray-600">{warning.action}</span>
                ) : null}
                <WarningDetail detail={warning.detail} />
                <span className="mt-1.5 block text-xs text-gray-500">
                  {warning.code} · tick to confirm you&apos;ve checked this against the original.
                </span>
              </label>
              {warning.field_name || warning.line_number !== null ? (
                <button
                  type="button"
                  onClick={() => goTo(warning)}
                  className="shrink-0 text-xs font-medium text-blue-700 hover:underline"
                >
                  Show {fieldLabel(warning) ? `“${fieldLabel(warning)}”` : "it"} →
                </button>
              ) : null}
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
  const entries = Object.entries(detail ?? {}).filter(
    ([k, v]) => v !== null && v !== "" && !DETAIL_SHOWN_ELSEWHERE.has(k),
  );
  if (entries.length === 0) return null;

  return (
    <span className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
      {entries.map(([key, value]) => (
        <span key={key} className="text-xs text-gray-600">
          {key.replace(/_/g, " ")}:{" "}
          <span className="numeric text-gray-900">{String(value)}</span>
        </span>
      ))}
    </span>
  );
}
