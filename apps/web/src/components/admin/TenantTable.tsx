"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import type { TenantRow } from "@/lib/admin";
import { PILL } from "@/components/StatusBadge";

/**
 * The tenant list (CLAUDE.md Section 7.15.3): name, tier, lifecycle status,
 * onboarding status, go-live date, subscription badge, usage against the
 * tier's allowance, review backlog and its age, 30-day confidence with a
 * trend arrow, AI cost this month, and when the last order arrived.
 *
 * One component for the Console home and the Tenants page, so the two can't
 * drift apart.
 */

const SUBSCRIPTION_STYLE: Record<string, string> = {
  active: "bg-green-100 text-green-800",
  trialing: "bg-sky-100 text-sky-800",
  past_due: "bg-amber-100 text-amber-900",
  unpaid: "bg-red-100 text-red-800",
  canceled: "bg-gray-200 text-gray-700",
};

type SortKey = "name" | "documents_this_month" | "needs_review" | "ai_cost_this_month" | "last_document_at";

function usd(value: string | null): string {
  return value === null ? "—" : `$${Number(value).toFixed(2)}`;
}

function ago(value: string | null): string {
  if (!value) return "—";
  const days = (Date.now() - new Date(value).getTime()) / 86_400_000;
  if (days < 1) return "today";
  if (days < 2) return "yesterday";
  return `${Math.floor(days)} days ago`;
}

export function TenantTable({ tenants, filter }: { tenants: TenantRow[]; filter?: string }) {
  const [sort, setSort] = useState<SortKey>("name");
  const [descending, setDescending] = useState(false);
  const [needle, setNeedle] = useState(filter ?? "");

  const rows = useMemo(() => {
    const text = needle.trim().toLowerCase();
    const matched = text
      ? tenants.filter(
          (t) =>
            t.name.toLowerCase().includes(text) ||
            t.status.includes(text) ||
            t.onboarding_status.includes(text),
        )
      : tenants;
    const sorted = [...matched].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "last_document_at") {
        return (
          new Date(b.last_document_at ?? 0).getTime() - new Date(a.last_document_at ?? 0).getTime()
        );
      }
      if (sort === "ai_cost_this_month") {
        return Number(b.ai_cost_this_month) - Number(a.ai_cost_this_month);
      }
      return Number(b[sort]) - Number(a[sort]);
    });
    return descending ? sorted.reverse() : sorted;
  }, [tenants, needle, sort, descending]);

  function header(key: SortKey, label: string, align: "left" | "right" = "left") {
    return (
      <th className={`px-3 py-2 ${align === "right" ? "text-right" : ""}`}>
        <button
          type="button"
          onClick={() => {
            if (sort === key) setDescending((d) => !d);
            else {
              setSort(key);
              setDescending(false);
            }
          }}
          className="uppercase tracking-wide hover:underline"
        >
          {label}
          {sort === key ? (descending ? " ↑" : " ↓") : ""}
        </button>
      </th>
    );
  }

  return (
    <>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        <input
          value={needle}
          onChange={(e) => setNeedle(e.target.value)}
          placeholder="Filter by name or status"
          data-testid="tenant-filter"
          className="w-64 rounded border px-3 py-1.5"
        />
        <span className="text-gray-500">{rows.length} shown</span>
      </div>

      <div className="mt-3 overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table data-testid="tenant-table" className="w-full min-w-[64rem] text-left text-sm">
          <thead className="border-b border-gray-200 text-xs text-gray-500">
            <tr>
              {header("name", "Tenant")}
              <th className="px-3 py-2 uppercase tracking-wide">Plan</th>
              <th className="px-3 py-2 uppercase tracking-wide">Status</th>
              <th className="px-3 py-2 uppercase tracking-wide">Billing</th>
              {header("documents_this_month", "This month", "right")}
              {header("needs_review", "To review", "right")}
              <th className="px-3 py-2 text-right uppercase tracking-wide">Confidence</th>
              {header("ai_cost_this_month", "AI cost", "right")}
              {header("last_document_at", "Last order")}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((t) => {
              const allowance = t.tier_document_allowance;
              const over = allowance !== null && t.documents_this_month > allowance;
              const backlogDays = Number(t.needs_review_oldest_days ?? 0);
              const confidence = t.mean_confidence_30;
              const drift =
                t.mean_confidence_7 !== null && confidence !== null
                  ? Number(t.mean_confidence_7) - Number(confidence)
                  : null;
              return (
                <tr key={t.id} data-testid="tenant-row">
                  <td className="px-3 py-2">
                    <Link href={`/admin/tenants/${t.id}`} className="font-medium text-blue-700 hover:underline">
                      {t.name}
                    </Link>
                    <span className="block text-xs text-gray-500">
                      {t.went_live_at ? `live ${new Date(t.went_live_at).toLocaleDateString()}` : "not live yet"}
                    </span>
                  </td>
                  <td className="px-3 py-2">{t.tier_name ?? "—"}</td>
                  <td className="px-3 py-2">
                    {t.status}
                    <span className="block text-xs text-gray-500">
                      {t.onboarding_status.replaceAll("_", " ")}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {t.stripe_subscription_status ? (
                      <span
                        className={`${PILL} ${SUBSCRIPTION_STYLE[t.stripe_subscription_status] ?? "bg-gray-100 text-gray-700"}`}
                      >
                        {t.stripe_subscription_status.replaceAll("_", " ")}
                      </span>
                    ) : (
                      <span className="text-gray-500">none</span>
                    )}
                  </td>
                  <td className={`px-3 py-2 text-right tabular-nums ${over ? "text-amber-800" : ""}`}>
                    {t.documents_this_month}
                    {allowance !== null ? <span className="text-gray-500"> / {allowance}</span> : null}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {t.needs_review}
                    {backlogDays >= 1 ? (
                      <span className={backlogDays >= 3 ? "block text-xs text-amber-800" : "block text-xs text-gray-500"}>
                        oldest {Math.floor(backlogDays)}d
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {confidence === null ? "—" : `${Math.round(Number(confidence) * 100)}%`}
                    {drift !== null && Math.abs(drift) >= 0.01 ? (
                      <span className={drift < 0 ? "text-amber-700" : "text-green-700"}>
                        {drift < 0 ? " ▼" : " ▲"}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{usd(t.ai_cost_this_month)}</td>
                  <td className="px-3 py-2 text-gray-600">{ago(t.last_document_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
