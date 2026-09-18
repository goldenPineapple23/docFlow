"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listTenants, type TenantRow } from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * Every tenant. The full dashboard table (usage vs allowance, confidence,
 * AI cost, subscription status, sorting and filters) is slice 5.5; this is
 * the list the setup tool needs now.
 */
export default function TenantsPage() {
  const [tenants, setTenants] = useState<TenantRow[] | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    let cancelled = false;
    listTenants()
      .then((rows) => {
        if (!cancelled) setTenants(rows);
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
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold">Tenants</h1>
        <Link href="/admin/tenants/new" className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">
          New tenant
        </Link>
      </div>
      {error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}
      <table className="mt-5 w-full overflow-hidden rounded-xl border border-gray-200 bg-white text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th className="px-4 py-2">Name</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Onboarding</th>
            <th className="px-4 py-2">Created</th>
          </tr>
        </thead>
        <tbody>
          {(tenants ?? []).map((t) => (
            <tr key={t.id} className="border-t border-gray-100">
              <td className="px-4 py-2">
                <Link href={`/admin/tenants/${t.id}`} className="font-medium text-blue-700 hover:underline">
                  {t.name}
                </Link>
              </td>
              <td className="px-4 py-2">{t.status}</td>
              <td className="px-4 py-2">{t.onboarding_status.replaceAll("_", " ")}</td>
              <td className="px-4 py-2 text-gray-600">{new Date(t.created_at).toLocaleDateString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
