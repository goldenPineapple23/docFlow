"use client";

import Link from "next/link";
import type { ActivityItem } from "@/lib/home";

/**
 * One rendering of "who did what, when", shared by the dashboard's short list
 * and the Activity page (slices 5.8a/5.8b, D-128/D-130). Two renderings of the
 * same rows would eventually say different things about the same event.
 *
 * Work the founder did while supporting the account is labelled "DocFlow
 * support" and never hidden (7.15.1). No row carries an extracted value: an
 * edit says how many fields changed, not what they became (Section 7.10).
 */

export const ACTIVITY_VERBS: Record<ActivityItem["kind"], string> = {
  approved: "approved",
  rejected: "rejected",
  edited: "edited",
  reopened: "reopened",
  exported: "exported",
  released: "released",
};

export function ago(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.round(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

/** The exact moment, for the title attribute: "how long ago" is easier to read,
 * but an audit trail has to be able to answer "when exactly". */
function exactly(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

export function ActivityList({
  items,
  testId,
  showDate = false,
}: {
  items: ActivityItem[];
  testId?: string;
  showDate?: boolean;
}) {
  return (
    <ul
      data-testid={testId}
      className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm"
    >
      {items.map((item, i) => (
        <li key={`${item.at}-${i}`} className="flex flex-wrap gap-x-2 px-4 py-2">
          <span className="font-medium">
            {item.by_docflow_support ? "DocFlow support" : (item.by ?? "Someone")}
          </span>
          <span>{ACTIVITY_VERBS[item.kind] ?? item.kind}</span>
          {item.document_id ? (
            <Link href={`/review/${item.document_id}`} className="text-blue-700 underline">
              {item.po_number ?? item.document_name ?? "an order"}
            </Link>
          ) : (
            <span>{item.po_number ?? item.document_name ?? "an order"}</span>
          )}
          {item.detail ? <span className="text-gray-500">({item.detail})</span> : null}
          <span className="ml-auto text-gray-500" title={exactly(item.at)}>
            {showDate ? `${exactly(item.at)} · ${ago(item.at)}` : ago(item.at)}
          </span>
        </li>
      ))}
    </ul>
  );
}
