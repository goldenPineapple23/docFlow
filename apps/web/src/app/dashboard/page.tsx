"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { getHome, type ActivityItem, type Home } from "@/lib/home";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";

/**
 * The customer's own dashboard (`docflow-mvp-features.docx`: "Simple dashboard
 * -- documents by status, recent activity"). Slice 5.8a, D-128.
 *
 * Admins only; a reviewer who reaches it is shown the catalog entry saying why
 * (AUTH-003) rather than an empty page. This is the account's shape, not the
 * founder's cross-tenant view, and it carries no money or cost.
 */

const STATUS_LABELS: Record<string, string> = {
  needs_review: "Needs review",
  approved: "Approved",
  exported: "Exported to file",
  rejected: "Rejected",
  failed: "Couldn't be read",
};

const ACTIVITY_VERBS: Record<ActivityItem["kind"], string> = {
  approved: "approved",
  rejected: "rejected",
  edited: "edited",
  reopened: "reopened",
  exported: "exported",
  released: "released",
};

function ago(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.round(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-gray-500">{hint}</p> : null}
    </div>
  );
}

export default function DashboardPage() {
  const [home, setHome] = useState<Home | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    getHome()
      .then((data) => {
        if (!cancelled) setHome(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ReviewApiError ? e.catalog : UNEXPECTED);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const statuses = home?.documents_by_status ?? {};
  const month = home?.this_month;

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-5xl p-6">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-600">
          How this account is doing right now. Everything here is your own — DocFlow never mixes
          accounts.
        </p>

        {error ? (
          <div className="mt-4 max-w-2xl">
            <CatalogErrorBox error={error} />
          </div>
        ) : null}
        {home === null && !error ? <p className="mt-4 text-sm text-gray-500">Loading…</p> : null}

        {home ? (
          <>
            {home.allowance.banner ? (
              <div
                data-testid="allowance-banner"
                className="mt-4 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950"
              >
                <p className="font-medium">{home.allowance.banner.title}</p>
                <p className="mt-0.5">{home.allowance.banner.message}</p>
                <p className="mt-0.5 text-sky-900">{home.allowance.banner.action}</p>
              </div>
            ) : null}

            <section className="mt-5">
              <h2 className="text-sm font-semibold">Orders by status</h2>
              <div
                data-testid="status-tiles"
                className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
              >
                {Object.entries(STATUS_LABELS).map(([key, label]) => (
                  <Link key={key} href={`/review?status=${key}`} className="block hover:opacity-80">
                    <Tile label={label} value={String(statuses[key] ?? 0)} />
                  </Link>
                ))}
              </div>
              {home.held.total > 0 ? (
                <div
                  data-testid="held-tile"
                  className="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950"
                >
                  <p className="font-medium">
                    {home.held.total} {home.held.total === 1 ? "document is" : "documents are"} being
                    held
                  </p>
                  {home.held.groups.map((g) => (
                    <p key={g.reason} className="mt-0.5">
                      {g.count} — {g.message}
                    </p>
                  ))}
                  <Link href="/held" className="mt-1 inline-block font-medium underline">
                    Open Held for review →
                  </Link>
                </div>
              ) : null}
            </section>

            <section className="mt-6">
              <h2 className="text-sm font-semibold">This month</h2>
              <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Tile
                  label="Arrived"
                  value={String(month?.arrived ?? 0)}
                  hint={`${home.received_today} today`}
                />
                <Tile
                  label="Counted toward your plan"
                  value={
                    home.allowance.allowance
                      ? `${month?.counted ?? 0} / ${home.allowance.allowance}`
                      : String(month?.counted ?? 0)
                  }
                  hint={home.allowance.tier ?? undefined}
                />
                <Tile label="Approved" value={String(month?.approved ?? 0)} />
                <Tile
                  label="Typical time to approve"
                  value={
                    month?.median_hours_to_approval === null ||
                    month?.median_hours_to_approval === undefined
                      ? "—"
                      : `${month.median_hours_to_approval} h`
                  }
                  hint="from arriving to approved"
                />
              </div>
              {home.oldest_needs_review_at ? (
                <p data-testid="oldest-waiting" className="mt-2 text-sm text-gray-600">
                  The oldest order waiting for review arrived{" "}
                  <strong>{ago(home.oldest_needs_review_at)}</strong>.{" "}
                  <Link href="/review" className="text-blue-700 underline">
                    Review it
                  </Link>
                </p>
              ) : (
                <p className="mt-2 text-sm text-gray-600">Nothing is waiting for review.</p>
              )}
            </section>

            <section className="mt-6">
              <h2 className="text-sm font-semibold">Recent activity</h2>
              {home.activity.length === 0 ? (
                <p className="mt-2 rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-600">
                  Nothing has happened on this account yet.
                </p>
              ) : (
                <ul
                  data-testid="activity"
                  className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm"
                >
                  {home.activity.map((item, i) => (
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
                      <span className="ml-auto text-gray-500">{ago(item.at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        ) : null}
      </main>
    </>
  );
}
