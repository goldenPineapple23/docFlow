"use client";

import { use } from "react";
import Link from "next/link";
import { ImportWorkbench } from "@/components/admin/ImportWorkbench";

/** Onboarding Step 5 (Section 7.15.2): the shared import screen. */
export default function TenantBuyersPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <>
      <Link href={`/admin/tenants/${id}`} className="text-sm text-blue-700 underline">
        ← Tenant
      </Link>
      <div className="mt-2">
        <ImportWorkbench tenantId={id} kind="buyers" />
      </div>
    </>
  );
}
