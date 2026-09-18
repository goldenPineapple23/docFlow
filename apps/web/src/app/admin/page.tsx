"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { acknowledgeAlert, listAlerts, type FounderAlert } from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * The Console home: the attention panel (CLAUDE.md Section 7.15.3).
 *
 * Every alert here is a `founder_alerts` row -- the same row the alert email
 * was sent from (Section 7.9: "one alert, one row, two channels"). Most
 * severe first. The health strip, tenant list and KPI cards join this page
 * in slice 5.5.
 */

const SEVERITY_STYLE: Record<FounderAlert["severity"], string> = {
  critical: "bg-red-700 text-white",
  high: "bg-red-100 text-red-800",
  warning: "bg-amber-100 text-amber-800",
  info: "bg-sky-100 text-sky-800",
};

export default function ConsoleHome() {
  const [alerts, setAlerts] = useState<FounderAlert[] | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  const refresh = useCallback(async () => {
    try {
      setAlerts((await listAlerts()).alerts);
    } catch (e) {
      if (e instanceof ReviewApiError) setError(e.catalog);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    listAlerts()
      .then(({ alerts: rows }) => {
        if (!cancelled) setAlerts(rows);
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
              </div>
              <button
                type="button"
                onClick={async () => {
                  await acknowledgeAlert(alert.id);
                  await refresh();
                }}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
              >
                Acknowledge
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
