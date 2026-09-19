"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listTenants, type TenantRow } from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { TenantTable } from "@/components/admin/TenantTable";

/** Every tenant, with the Section 7.15.3 columns (D-121). */
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
      {tenants === null ? (
        !error ? <p className="mt-5 text-sm text-gray-500">Loading…</p> : null
      ) : (
        <TenantTable tenants={tenants} />
      )}
    </>
  );
}
