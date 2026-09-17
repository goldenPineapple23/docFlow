"use client";

import type { DocumentHeader } from "@/lib/review";
import { ConfidenceBadge, ProvenanceNote, isLowConfidence } from "./confidence";
import { formatAmount } from "@/lib/money";

/**
 * The editable header (CLAUDE.md Section 7.3 / 7.12).
 *
 * Every value here came out of an untrusted document and is rendered as
 * text, never as HTML -- React escapes by default and this file contains no
 * `dangerouslySetInnerHTML`, which Section 10 forbids outright in the review
 * surface.
 *
 * Money and dates are `<input type="text">` on purpose. `type="number"`
 * would hand the value to the browser's numeric parser, which is a float,
 * and Section 7.1 does not allow a float anywhere near money. `type="date"`
 * would silently reformat or reject what the document actually printed.
 */

// The header fields that hold an amount.
const MONEY_FIELDS = new Set(["order_total"]);

export const HEADER_FIELDS: Array<{ name: keyof DocumentHeader & string; label: string; wide?: boolean }> = [
  { name: "po_number", label: "PO number" },
  { name: "buyer_name", label: "Buyer" },
  { name: "order_date", label: "Order date" },
  { name: "requested_delivery_date", label: "Requested delivery" },
  { name: "order_total", label: "Order total" },
  { name: "currency", label: "Currency" },
  { name: "payment_terms", label: "Payment terms" },
  { name: "buyer_contact_email", label: "Buyer email" },
  { name: "ship_to_address", label: "Ship to", wide: true },
  { name: "notes", label: "Notes", wide: true },
];

export function HeaderFields({
  header,
  edits,
  disabled,
  onChange,
}: {
  header: DocumentHeader;
  edits: Record<string, string | null>;
  disabled: boolean;
  onChange: (field: string, value: string) => void;
}) {
  return (
    <section
      aria-labelledby="header-heading"
      // Each section gets its own tint. The first walkthrough tester could
      // not tell where the order's own details ended and the line items
      // began; a hairline border alone was not enough, so the three kinds of
      // information are now three surfaces -- slate for the order, indigo
      // for its lines, amber for the things needing attention.
      className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-5"
    >
      <div className="space-y-0.5">
        <h2
          id="header-heading"
          className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-600"
        >
          Order details
        </h2>
        <p className="text-sm text-slate-500">
          What this order says as a whole — who sent it, when, and the total.
        </p>
      </div>

      {header.currency_inferred ? (
        <p
          data-testid="currency-inferred"
          className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          The currency wasn&apos;t stated on this order — DocFlow read it from a symbol. Confirm
          it before approving.
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {HEADER_FIELDS.map(({ name, label, wide }) => {
          const stored = header[name];
          const current = name in edits ? edits[name] : (stored as string | null);
          const confidence = header.confidence?.[name];
          const low = isLowConfidence(confidence);

          return (
            <div key={name} className={wide ? "sm:col-span-2" : undefined}>
              <div className="flex items-baseline justify-between gap-2">
                <label htmlFor={`header-${name}`} className="block text-sm font-medium">
                  {label}
                </label>
                <span className="flex items-center gap-2">
                  <ProvenanceNote provenance={header.provenance?.[name]} />
                  <ConfidenceBadge value={confidence} />
                </span>
              </div>
              {/*
                A grouped reading of the amount, BESIDE the field and never
                inside it. The input has to hold exactly what will be sent to
                a NUMERIC column, so it shows "1356.00"; this shows
                "1,356.00" so the figure can be checked against the document
                at a glance. Only rendered when grouping changes anything.
              */}
              {MONEY_FIELDS.has(name) && formatAmount(current) !== (current ?? "") ? (
                <p data-testid={`header-grouped-${name}`} className="mt-1 text-xs text-gray-500">
                  reads as {formatAmount(current)}
                </p>
              ) : null}
              <input
                id={`header-${name}`}
                name={name}
                type="text"
                disabled={disabled}
                value={current ?? ""}
                onChange={(e) => onChange(name, e.target.value)}
                data-testid={`header-input-${name}`}
                data-dirty={name in edits ? "true" : "false"}
                className={[
                  "mt-1.5 w-full rounded-md border px-2.5 py-1.5 text-sm transition-colors",
                  "focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100",
                  "disabled:bg-gray-50 disabled:text-gray-500",
                  low ? "border-amber-300 bg-amber-50" : "border-slate-300 bg-white",
                  name in edits ? "border-blue-400 bg-blue-50/40" : "",
                ].join(" ")}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}
