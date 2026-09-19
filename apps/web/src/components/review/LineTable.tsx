"use client";

import { useState } from "react";
import type { CatalogItem, DocumentLine } from "@/lib/review";
import { searchItems } from "@/lib/review";
import { ConfidenceBadge, ProvenanceNote, isLowConfidence } from "./confidence";

/**
 * The editable line items, with matching state and the "create mapping"
 * control (CLAUDE.md Section 7.6 / 7.13).
 *
 * **A table, because it is one.** These were stacked cards, one per line, and
 * a four-line order filled the screen. A reviewer comparing a column of
 * quantities against a column on the document was reading down a page of
 * boxes instead of across a row. Rows also do the work colour was being
 * asked to do: the first walkthrough tester could not tell the order's own
 * details from its lines, and a table settles that by looking like a table.
 *
 * Two things this must never do, both from Section 7.6:
 *
 *   * apply a sub-threshold match on its own -- candidates are shown with
 *     their scores and stay suggestions until a person picks one;
 *   * hide a unit-of-measure disagreement. `uom_mismatch` gets a visible
 *     flag, and nothing is normalized.
 *
 * Confirming a candidate creates a learned rule, which is why it is an
 * explicit click per line rather than anything that happens on focus or blur.
 * Section 10: no rule activates without a human confirmation.
 */

// Widths are tuned to the values these columns actually hold: a quantity is
// numeric(14,4) so it renders as "24.0000", and a description is the longest
// thing on the row. An earlier split clipped both -- "4.000(" and "1k(" --
// which on a screen whose job is checking figures against a document is
// worse than useless.
const COLUMNS: Array<{ name: string; label: string; numeric?: boolean; width: string }> = [
  { name: "sku", label: "SKU", width: "w-[14%]" },
  { name: "description", label: "Description", width: "w-[28%]" },
  { name: "quantity", label: "Qty", numeric: true, width: "w-[13%]" },
  { name: "unit", label: "Unit", width: "w-[8%]" },
  { name: "unit_price", label: "Unit price", numeric: true, width: "w-[14%]" },
  { name: "line_total", label: "Line total", numeric: true, width: "w-[14%]" },
];

export function LineTable({
  lines,
  edits,
  disabled,
  onChange,
  onConfirmMapping,
}: {
  lines: DocumentLine[];
  edits: Record<string, Record<string, string | null>>;
  disabled: boolean;
  onChange: (lineId: string, field: string, value: string) => void;
  onConfirmMapping: (lineId: string, itemId: string) => Promise<void>;
}) {
  return (
    <section
      aria-labelledby="lines-heading"
      className="space-y-4 rounded-xl border border-sky-200 bg-sky-50 p-5"
    >
      <div className="space-y-0.5">
        <h2
          id="lines-heading"
          className="text-[11px] font-semibold uppercase tracking-[0.08em] text-sky-800"
        >
          Line items · {lines.length}
        </h2>
        <p className="text-sm text-sky-900/60">
          The products being ordered, one row per line on the document.
        </p>
      </div>

      {lines.length === 0 ? (
        <p className="text-sm text-gray-700">
          DocFlow didn&apos;t find any line items on this order. Check the original before approving.
        </p>
      ) : (
        // A floor on the table's width, and sideways scrolling below it: the
        // columns are percentages, so without one they squeeze down until
        // numbers and inputs are unreadable on a narrow screen.
        <div className="overflow-x-auto rounded-lg border border-sky-200 bg-white shadow-sm">
          <table className="w-full min-w-[40rem] table-fixed border-collapse">
            <caption className="sr-only">Line items on this order</caption>
            <thead>
              <tr className="bg-sky-100/70 text-left text-[11px] uppercase tracking-[0.05em] text-sky-900/70">
                <th scope="col" className="w-[5%] px-2 py-2 font-semibold">
                  #
                </th>
                {COLUMNS.map((column) => (
                  <th
                    key={column.name}
                    scope="col"
                    className={`px-2 py-2 font-semibold ${column.width} ${
                      column.numeric ? "text-right" : ""
                    }`}
                  >
                    {column.label}
                  </th>
                ))}
                <th scope="col" className="w-[4%] px-1 py-2" />
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <LineRow
                  key={line.id}
                  line={line}
                  edits={edits[line.id] ?? {}}
                  disabled={disabled}
                  onChange={onChange}
                  onConfirmMapping={onConfirmMapping}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function LineRow({
  line,
  edits,
  disabled,
  onChange,
  onConfirmMapping,
}: {
  line: DocumentLine;
  edits: Record<string, string | null>;
  disabled: boolean;
  onChange: (lineId: string, field: string, value: string) => void;
  onConfirmMapping: (lineId: string, itemId: string) => Promise<void>;
}) {
  const low = isLowConfidence(line.confidence);
  // Anything a reviewer would want to look at before approving this line.
  const needsAttention = low || line.uom_mismatch || !line.matched_item_id;
  // The details row starts open when there is something to see, and the
  // arrow always opens and closes it. It used to be forced open for those
  // lines, so the arrow only flipped direction and seemed broken.
  const [open, setOpen] = useState(needsAttention);
  // For a line already matched: the catalog search, shown on "Change".
  const [changing, setChanging] = useState(false);

  return (
    <>
      <tr
        data-testid={`line-${line.line_number}`}
        className={[
          "border-t border-sky-50",
          low ? "bg-amber-50/70" : "odd:bg-white even:bg-sky-50/30",
        ].join(" ")}
      >
        <td className="px-2 py-1.5 align-middle text-xs text-gray-400">{line.line_number}</td>

        {COLUMNS.map(({ name, numeric }) => {
          const stored = line[name as keyof DocumentLine] as string | null;
          const current = name in edits ? edits[name] : stored;
          return (
            <td key={name} className="px-2 py-1.5 align-middle">
              <label htmlFor={`line-${line.id}-${name}`} className="sr-only">
                {name.replace(/_/g, " ")} for line {line.line_number}
              </label>
              <input
                id={`line-${line.id}-${name}`}
                // Text, never number: the browser's numeric input parses to a
                // float, and Section 7.1 keeps money out of floats entirely.
                type="text"
                inputMode={numeric ? "decimal" : undefined}
                disabled={disabled}
                value={current ?? ""}
                onChange={(e) => onChange(line.id, name, e.target.value)}
                data-testid={`line-${line.line_number}-${name}`}
                data-dirty={name in edits ? "true" : "false"}
                className={[
                  "w-full rounded border px-1.5 py-1 text-[13px] transition-colors",
                  "focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100",
                  "disabled:bg-gray-50 disabled:text-gray-500",
                  // Numbers are read down a column and compared against the
                  // document, so they are tabular and right-aligned.
                  numeric ? "numeric text-right" : "",
                  name in edits ? "border-blue-400 bg-blue-50/50" : "border-slate-200 bg-white",
                ].join(" ")}
              />
            </td>
          );
        })}

        <td className="px-1 py-1.5 text-center align-middle">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={`Catalog match and checks for line ${line.line_number}`}
            title={open ? "Hide this line's catalog match and checks" : "Show this line's catalog match and checks"}
            className={[
              "rounded px-1 text-xs leading-5",
              needsAttention
                ? "bg-amber-200 text-amber-900"
                : "text-gray-300 hover:bg-gray-100 hover:text-gray-600",
            ].join(" ")}
          >
            {open ? "▴" : "▾"}
          </button>
        </td>
      </tr>

      {/*
        Matching state and per-line checks sit in a row of their own. They are
        detail a reviewer wants occasionally; inline, they made every order
        several screens tall. The row opens automatically when there IS
        something to see, so nothing hides behind a click -- the chevron
        collapses it, or opens a line that is already fine to change its match.
      */}
      {open ? (
        <tr className={low ? "bg-amber-50/70" : "bg-white"}>
          <td />
          <td colSpan={COLUMNS.length + 1} className="px-2 pb-3 pt-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <ConfidenceBadge value={line.confidence} />
              <ProvenanceNote provenance={line.provenance?.matched_item_id} />
              {line.matched_item_id ? (
                <span data-testid={`match-${line.line_number}`} className="text-xs text-green-800">
                  Matched to the catalog
                  {line.match_method ? ` (${line.match_method.replace("_", " ")})` : ""}
                  {line.match_score ? ` · score ${line.match_score}` : ""}
                </span>
              ) : (
                <span className="text-xs text-gray-600">No catalog match.</span>
              )}
              {!disabled && line.matched_item_id && !changing ? (
                <button
                  type="button"
                  className="text-xs text-blue-700 underline"
                  onClick={() => setChanging(true)}
                >
                  Change
                </button>
              ) : null}
            </div>

            {line.uom_mismatch ? (
              <p
                data-testid={`uom-mismatch-${line.line_number}`}
                className="mt-2 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900"
              >
                This line&apos;s unit doesn&apos;t match the catalog item it matched
                {line.matched_uom ? ` (catalog says ${line.matched_uom})` : ""}. Confirm which is
                right — a case ordered as an each ships the wrong quantity.
              </p>
            ) : null}

            <MatchPicker
              line={line}
              disabled={disabled}
              expanded={changing || !line.matched_item_id}
              onConfirmMapping={onConfirmMapping}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function MatchPicker({
  line,
  disabled,
  expanded,
  onConfirmMapping,
}: {
  line: DocumentLine;
  disabled: boolean;
  expanded: boolean;
  onConfirmMapping: (lineId: string, itemId: string) => Promise<void>;
}) {
  if (!expanded) return null;

  return (
    <div className="mt-2">
      {line.match_candidates.length > 0 ? (
        <div data-testid={`candidates-${line.line_number}`} className="text-xs">
          <p className="text-gray-600">
            Possible matches — none applied, because none scored high enough to be certain:
          </p>
          <ul className="mt-1 space-y-1">
            {line.match_candidates.slice(0, 5).map((candidate, i) => (
              <li key={candidate.item_id ?? i} className="flex flex-wrap items-center gap-2">
                <span className="numeric">{candidate.sku}</span>
                <span className="text-gray-600">{candidate.description}</span>
                {candidate.score ? <span className="text-gray-500">· {candidate.score}</span> : null}
                {!disabled && candidate.item_id ? (
                  <button
                    type="button"
                    className="text-blue-700 underline"
                    onClick={() => onConfirmMapping(line.id, candidate.item_id as string)}
                  >
                    This one
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!disabled ? (
        <SkuSearch lineId={line.id} lineNumber={line.line_number} onConfirm={onConfirmMapping} />
      ) : null}
    </div>
  );
}

function SkuSearch({
  lineId,
  lineNumber,
  onConfirm,
}: {
  lineId: string;
  lineNumber: number;
  onConfirm: (lineId: string, itemId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogItem[]>([]);
  const [searching, setSearching] = useState(false);

  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    try {
      const { items } = await searchItems(query.trim());
      setResults(items);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="mt-2">
      <form onSubmit={runSearch} className="flex max-w-md gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search the catalog by SKU or description"
          aria-label={`Search the catalog for line ${lineNumber}`}
          data-testid={`sku-search-${lineNumber}`}
          className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
        />
        <button
          type="submit"
          disabled={searching}
          className="rounded border border-gray-300 bg-white px-2 py-1 text-xs disabled:text-gray-400"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </form>

      {results.length > 0 ? (
        <ul data-testid={`sku-results-${lineNumber}`} className="mt-1 space-y-1 text-xs">
          {results.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-2">
              <span className="numeric">{item.sku}</span>
              <span className="text-gray-600">{item.description}</span>
              <button
                type="button"
                className="text-blue-700 underline"
                onClick={() => onConfirm(lineId, item.id)}
              >
                Use this &amp; remember it
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
