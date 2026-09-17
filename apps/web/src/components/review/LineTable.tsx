"use client";

import { useState } from "react";
import type { CatalogItem, DocumentLine } from "@/lib/review";
import { searchItems } from "@/lib/review";
import { ConfidenceBadge, ProvenanceNote, isLowConfidence } from "./confidence";

/**
 * The editable line items, with matching state and the "create mapping"
 * control (CLAUDE.md Section 7.6 / 7.13).
 *
 * Two things this table must never do, both from Section 7.6:
 *
 *   * apply a sub-threshold match on its own -- candidates are shown with
 *     their scores and stay suggestions until a person picks one;
 *   * hide a unit-of-measure disagreement. `uom_mismatch` gets a visible
 *     flag, and nothing is normalized.
 *
 * Confirming a candidate creates a learned rule, which is why it is an
 * explicit button per line rather than anything that happens on focus or
 * blur. Section 10: no rule activates without a human confirmation.
 */

const LINE_FIELDS: Array<{ name: string; label: string; numeric?: boolean }> = [
  { name: "sku", label: "SKU" },
  { name: "description", label: "Description" },
  { name: "quantity", label: "Qty", numeric: true },
  { name: "unit", label: "Unit" },
  { name: "unit_price", label: "Unit price", numeric: true },
  { name: "line_total", label: "Line total", numeric: true },
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
      className="space-y-3 rounded-lg border border-indigo-200 bg-indigo-50/50 p-4"
    >
      <div>
        <h2 id="lines-heading" className="text-sm font-semibold uppercase tracking-wide text-indigo-900">
          Line items ({lines.length})
        </h2>
        <p className="text-xs text-indigo-900/70">
          The products being ordered, one row per line on the document.
        </p>
      </div>

      {lines.length === 0 ? (
        <p className="text-sm text-gray-600">
          DocFlow didn&apos;t find any line items on this order. Check the original before approving.
        </p>
      ) : null}

      <div className="space-y-4">
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
      </div>
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

  return (
    <div
      data-testid={`line-${line.line_number}`}
      className={[
        "rounded border p-3",
        low ? "border-amber-400 bg-amber-50/40" : "border-gray-200",
      ].join(" ")}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">Line {line.line_number}</span>
        <span className="flex items-center gap-2">
          <ProvenanceNote provenance={line.provenance?.matched_item_id} />
          <ConfidenceBadge value={line.confidence} />
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-6">
        {LINE_FIELDS.map(({ name, label, numeric }) => {
          const stored = line[name as keyof DocumentLine] as string | null;
          const current = name in edits ? edits[name] : stored;
          return (
            <div key={name} className={name === "description" ? "col-span-2" : undefined}>
              <label
                htmlFor={`line-${line.id}-${name}`}
                className="block text-xs font-medium text-gray-600"
              >
                {label}
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
                  "mt-0.5 w-full rounded border px-2 py-1 text-sm",
                  "disabled:bg-gray-50 disabled:text-gray-500",
                  name in edits ? "border-blue-400 ring-1 ring-blue-300" : "border-gray-300",
                ].join(" ")}
              />
            </div>
          );
        })}
      </div>

      {line.uom_mismatch ? (
        <p
          data-testid={`uom-mismatch-${line.line_number}`}
          className="mt-2 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900"
        >
          This line&apos;s unit doesn&apos;t match the catalog item it matched
          {line.matched_uom ? ` (catalog says ${line.matched_uom})` : ""}. Confirm which is right —
          a case ordered as an each ships the wrong quantity.
        </p>
      ) : null}

      <MatchState line={line} disabled={disabled} onConfirmMapping={onConfirmMapping} />
    </div>
  );
}

function MatchState({
  line,
  disabled,
  onConfirmMapping,
}: {
  line: DocumentLine;
  disabled: boolean;
  onConfirmMapping: (lineId: string, itemId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);

  if (line.matched_item_id && !open) {
    return (
      <div className="mt-2 flex items-center gap-2 text-xs">
        <span data-testid={`match-${line.line_number}`} className="text-green-800">
          Matched to a catalog item
          {line.match_method ? ` (${line.match_method.replace("_", " ")})` : ""}
          {line.match_score ? ` · score ${line.match_score}` : ""}
        </span>
        {!disabled ? (
          <button type="button" className="text-blue-700 underline" onClick={() => setOpen(true)}>
            Change
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="mt-2">
      {line.match_candidates.length > 0 ? (
        <div data-testid={`candidates-${line.line_number}`} className="text-xs">
          <p className="text-gray-600">
            Possible matches — none applied, because none scored high enough to be certain:
          </p>
          <ul className="mt-1 space-y-1">
            {line.match_candidates.slice(0, 5).map((candidate, i) => (
              <li key={candidate.item_id ?? i} className="flex items-center gap-2">
                <span className="font-mono">{candidate.sku}</span>
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
      ) : (
        <p className="text-xs text-gray-600">No catalog match.</p>
      )}

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
      <form onSubmit={runSearch} className="flex gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search the catalog by SKU or description"
          aria-label={`Search the catalog for line ${lineNumber}`}
          data-testid={`sku-search-${lineNumber}`}
          className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
        />
        <button
          type="submit"
          disabled={searching}
          className="rounded border border-gray-300 px-2 py-1 text-sm disabled:text-gray-400"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </form>

      {results.length > 0 ? (
        <ul data-testid={`sku-results-${lineNumber}`} className="mt-1 space-y-1 text-xs">
          {results.map((item) => (
            <li key={item.id} className="flex items-center gap-2">
              <span className="font-mono">{item.sku}</span>
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
