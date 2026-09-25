"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { getTenantAudit, getTenantOverview, type AuditEntry } from "@/lib/admin";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * The tenant page's Audit tab (Section 7.15.3; D-143). Everything that changed
 * this account and who did it: lifecycle events (creation, onboarding steps,
 * cancel/reactivate, settings) and every Console action taken on it, newest
 * first. Read-only. Page views are audited too but hidden unless asked for,
 * or they would bury the changes.
 *
 * Every value is rendered as text; payloads hold IDs, flags and counts only
 * (neither table ever stores document data -- Section 7.10).
 */

const PAGE = 50;

// Plain-English names for what the trail records. Anything not listed shows
// its own name with the underscores removed, so a new event is never hidden.
const LABELS: Record<string, string> = {
  created: "Account created",
  deleted: "Account deleted",
  invite_sent: "Invite sent",
  user_invited: "User invited",
  user_removed: "User removed",
  deal_terms_changed: "Deal terms changed",
  tier_changed: "Plan changed",
  onboarding_catalog_loaded: "Onboarding: catalog loaded",
  onboarding_test_batch_uploaded: "Onboarding: test batch uploaded",
  onboarding_test_batch_running: "Onboarding: test batch running",
  onboarding_test_batch_complete: "Onboarding: test batch complete",
  onboarding_live: "Onboarding: went live",
  cancellation_scheduled: "Cancellation scheduled",
  suspended: "Suspended",
  pending_deletion_entered: "Export window started",
  reactivated: "Reactivated",
  intake_address_rotated: "Intake address replaced",
  sender_allowlist_changed: "Approved-sender list changed",
  example_prompting_enabled: "Example prompting turned on",
  example_prompting_disabled: "Example prompting turned off",
  tenant_create: "Created the account",
  tenant_delete: "Deleted the account",
  invite_send: "Sent the invite",
  deal_terms_update: "Updated deal terms",
  tier_change: "Changed plan",
  go_live: "Went live",
  cancel: "Cancelled",
  reactivate: "Reactivated",
  test_batch_upload: "Uploaded test batch",
  test_batch_run: "Ran test batch extraction",
  test_batch_complete: "Marked test batch complete",
  catalog_import_upload: "Uploaded a catalog/customer file",
  catalog_import_from_intake: "Imported a file from intake",
  import_mapping_set: "Changed import column mapping",
  import_row_fix: "Fixed an import row",
  import_commit: "Committed an import",
  import_discard: "Discarded an import",
  buyer_merge: "Merged customers",
  buyer_merge_dismiss: "Dismissed a possible duplicate",
  learned_rule_enable: "Turned a learned rule on",
  learned_rule_disable: "Turned a learned rule off",
  learned_rule_delete: "Deleted a learned rule",
  field_schema_save: "Saved field settings",
  quarantine_release: "Released held documents",
  quarantine_clear: "Cleared held documents",
  intake_address_rotate: "Replaced the intake address",
  sender_settings_update: "Changed approved-sender settings",
  example_prompting_set: "Changed example prompting",
  acting_as_write: "Changed an order (as DocFlow support)",
  acting_as_read: "Opened an order (as DocFlow support)",
  quarantine_read: "Viewed held documents",
  read: "Viewed",
};

function auditLabel(event: string): string {
  if (LABELS[event]) return LABELS[event];
  const words = event.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function value(v: unknown): string {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    // One level of nesting, e.g. a plan change's before/after.
    return Object.entries(v as Record<string, unknown>)
      .filter(([, inner]) => inner !== null && inner !== undefined && inner !== "")
      .map(([k, inner]) => `${k.replace(/_/g, " ")} ${typeof inner === "object" ? JSON.stringify(inner) : String(inner)}`)
      .join(", ");
  }
  return Array.isArray(v) ? v.map(String).join(", ") : String(v);
}

function details(payload: Record<string, unknown>): string {
  return Object.entries(payload)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${value(v)}`)
    .join(" · ");
}

export default function AuditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tenantName, setTenantName] = useState<string | null>(null);
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [includeViews, setIncludeViews] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getTenantOverview(id)
      .then((t) => {
        if (!cancelled) setTenantName(t.tenant.name);
      })
      .catch(() => {});
    getTenantAudit(id, { includeViews, limit: PAGE, offset })
      .then((body) => {
        if (cancelled) return;
        setEntries(body.entries);
        setTotal(body.total);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, [id, includeViews, offset]);

  return (
    <>
      <Link href={`/admin/tenants/${id}`} className="text-sm text-blue-700 underline">
        ← Tenant
      </Link>
      <h1 className="mt-1 text-xl font-semibold">Audit{tenantName ? ` — ${tenantName}` : ""}</h1>
      <p className="mt-1 max-w-3xl text-sm text-gray-600">
        Every change to this account and who made it, newest first: account events (onboarding,
        plan, cancel and reactivate, settings) and every action taken on it from the Console.
        Changes made by the customer&apos;s own reviewers to orders are on each order&apos;s own trail.
      </p>

      <label className="mt-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={includeViews}
          data-testid="audit-include-views"
          onChange={(e) => {
            setOffset(0);
            setIncludeViews(e.target.checked);
          }}
        />
        Also show Console page views
      </label>

      {error ? (
        <div className="mt-4">
          <CatalogErrorBox error={error} />
        </div>
      ) : null}

      {entries === null ? (
        !error ? <p className="mt-5 text-sm text-gray-500">Loading…</p> : null
      ) : entries.length === 0 ? (
        <p className="mt-5 text-sm text-gray-600" data-testid="audit-empty">
          Nothing recorded yet.
        </p>
      ) : (
        <section className="mt-4 overflow-x-auto rounded-xl border border-gray-200 bg-white">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-2">When</th>
                <th className="px-4 py-2">What</th>
                <th className="px-4 py-2">Who</th>
                <th className="px-4 py-2">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {entries.map((e, i) => (
                <tr key={`${e.at}-${i}`} data-testid="audit-row">
                  <td className="whitespace-nowrap px-4 py-2 tabular-nums text-gray-700">
                    {new Date(e.at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2">
                    {auditLabel(e.event)}
                    <span className="ml-2 text-xs text-gray-500">
                      {e.source === "lifecycle" ? "account" : "Console"}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    {e.actor_email ?? "DocFlow (automatic)"}
                    {e.actor_is_docflow ? (
                      <span className="ml-2 text-xs text-gray-500">DocFlow support</span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-600">{details(e.payload)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {total > PAGE ? (
        <div className="mt-3 flex items-center gap-3 text-sm">
          <button
            type="button"
            disabled={offset === 0}
            data-testid="audit-newer"
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
            className="rounded border border-gray-300 px-3 py-1 disabled:opacity-40"
          >
            ← Newer
          </button>
          <span className="text-gray-600">
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
          </span>
          <button
            type="button"
            disabled={offset + PAGE >= total}
            data-testid="audit-older"
            onClick={() => setOffset(offset + PAGE)}
            className="rounded border border-gray-300 px-3 py-1 disabled:opacity-40"
          >
            Older →
          </button>
        </div>
      ) : null}
    </>
  );
}
