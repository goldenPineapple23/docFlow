"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  acknowledgeAlert,
  getDashboard,
  listAlerts,
  listTenants,
  type Dashboard,
  type FounderAlert,
  type TenantRow,
} from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { HealthStrip } from "@/components/admin/HealthStrip";
import { KpiCards } from "@/components/admin/KpiCards";
import { TenantTable } from "@/components/admin/TenantTable";

/**
 * The Console home (CLAUDE.md Section 7.15.3), in the four regions the
 * section names, top to bottom:
 *
 *   1. Attention panel — every unacknowledged `founder_alerts` row, the same
 *      row its email was sent from (7.9: one event, two channels).
 *   2. Health strip — cheap live signals, with links out to Sentry, Supabase
 *      and Stripe rather than rebuilt versions of them.
 *   3. Tenant list — every tenant, with usage, backlog, confidence and cost.
 *   4. KPI cards — the success metrics, from the nightly rollup.
 *
 * Its job is to answer "is everything healthy, who needs attention, and how
 * is the business doing" in under ten seconds.
 */

const SEVERITY_STYLE: Record<FounderAlert["severity"], string> = {
  critical: "bg-red-700 text-white",
  high: "bg-red-100 text-red-800",
  warning: "bg-amber-100 text-amber-800",
  info: "bg-sky-100 text-sky-800",
};

/**
 * How many documents an alert's state has hit (D-203). Only an alert raised
 * once per state carries the count; it is read from the documents
 * themselves, so it keeps counting after the alert is acknowledged.
 */
function AffectedDocuments({ alert }: { alert: FounderAlert }) {
  if (alert.no_document === "email_intake") {
    // Refused at intake, before any document existed (PR 1b-i): there is
    // nothing to count, and "0" would read as "nothing was hit".
    return (
      <p data-testid="affected-documents" className="text-sm text-gray-700">
        Email intake, no document
      </p>
    );
  }
  const affected = alert.affected_documents;
  if (!affected) return null;
  return (
    <p data-testid="affected-documents" className="text-sm text-gray-700">
      Affected documents: {affected.total}
      {alert.acknowledged_at ? ` (${affected.since_acknowledged} since you acknowledged it)` : ""}
      {affected.latest_at ? ` · latest ${new Date(affected.latest_at).toLocaleString()}` : ""}
    </p>
  );
}

export default function ConsoleHome() {
  const [alerts, setAlerts] = useState<FounderAlert[] | null>(null);
  const [stillHappening, setStillHappening] = useState<FounderAlert[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [tenants, setTenants] = useState<TenantRow[] | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  const load = useCallback(async () => {
    const [a, d, t] = await Promise.all([listAlerts(), getDashboard(), listTenants()]);
    setAlerts(a.alerts);
    setStillHappening(a.still_happening ?? []);
    setDashboard(d);
    setTenants(t);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAlerts(), getDashboard(), listTenants()])
      .then(([a, d, t]) => {
        if (cancelled) return;
        setAlerts(a.alerts);
        setStillHappening(a.still_happening ?? []);
        setDashboard(d);
        setTenants(t);
      })
      .catch((e) => {
        if (!cancelled && e instanceof ReviewApiError) setError(e.catalog);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <h1 className="text-xl font-semibold">Needs your attention</h1>
      <p className="mt-1 text-sm text-gray-600">
        Everything DocFlow has flagged for you, most severe first. Each one was also emailed to
        you when it was raised.
      </p>

      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}
      {alerts === null && !error ? <p className="mt-6 text-sm text-gray-500">Loading…</p> : null}
      {alerts !== null && alerts.length === 0 ? (
        <p data-testid="no-alerts" className="mt-6 text-sm text-green-800">
          Nothing needs your attention.
        </p>
      ) : null}

      {alerts && alerts.length > 0 ? (
        <ul data-testid="alerts" className="mt-5 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
          {alerts.map((alert) => (
            <li key={alert.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="space-y-1">
                <p className="flex items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLE[alert.severity]}`}>
                    {alert.severity}
                  </span>
                  <span className="font-medium">{alert.type.replaceAll("_", " ")}</span>
                </p>
                <p className="text-sm text-gray-600">
                  {alert.tenant_id ? (
                    <Link href={`/admin/tenants/${alert.tenant_id}`} className="text-blue-700 hover:underline">
                      {alert.tenant_name ?? alert.tenant_id}
                    </Link>
                  ) : (
                    "No tenant"
                  )}{" "}
                  · {new Date(alert.created_at).toLocaleString()}
                </p>
                <p className="font-mono text-xs text-gray-500">
                  {Object.entries(alert.payload)
                    .map(([k, v]) => `${k}: ${String(v)}`)
                    .join(" · ")}
                </p>
                <AffectedDocuments alert={alert} />
              </div>
              <button
                type="button"
                onClick={async () => {
                  await acknowledgeAlert(alert.id);
                  await load();
                }}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
              >
                Acknowledge
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {stillHappening.length > 0 ? (
        <section className="mt-6">
          <h2 className="text-sm font-semibold">Acknowledged, still happening</h2>
          <p className="mt-1 text-sm text-gray-600">
            You acknowledged these, and the same problem has hit more documents since. They are
            not raised or emailed again.
          </p>
          <ul
            data-testid="alerts-still-happening"
            className="mt-3 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white"
          >
            {stillHappening.map((alert) => (
              <li key={alert.id} className="space-y-1 p-4">
                <p className="flex items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLE[alert.severity]}`}>
                    {alert.severity}
                  </span>
                  <span className="font-medium">{alert.type.replaceAll("_", " ")}</span>
                </p>
                <p className="text-sm text-gray-600">
                  {alert.tenant_id ? (
                    <Link href={`/admin/tenants/${alert.tenant_id}`} className="text-blue-700 hover:underline">
                      {alert.tenant_name ?? alert.tenant_id}
                    </Link>
                  ) : (
                    "No tenant"
                  )}
                  {alert.acknowledged_at
                    ? ` · acknowledged ${new Date(alert.acknowledged_at).toLocaleString()}`
                    : ""}
                </p>
                <p className="font-mono text-xs text-gray-500">
                  {Object.entries(alert.payload)
                    .map(([k, v]) => `${k}: ${String(v)}`)
                    .join(" · ")}
                </p>
                <AffectedDocuments alert={alert} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {dashboard ? <HealthStrip data={dashboard} onRecomputed={() => void load()} /> : null}

      {tenants ? (
        <section className="mt-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">Tenants</h2>
            <Link href="/admin/tenants/new" className="text-sm text-blue-700 hover:underline">
              New tenant →
            </Link>
          </div>
          <TenantTable tenants={tenants} />
        </section>
      ) : null}

      {dashboard ? <KpiCards data={dashboard} /> : null}
    </>
  );
}
