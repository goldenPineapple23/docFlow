"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { getTenantOverview } from "@/lib/admin";
import { useReviewScope } from "@/lib/reviewScope";

/**
 * The top of a review screen. A tenant user gets the app's own header. The
 * founder in the Console gets a banner that never lets them forget whose
 * data this is and that every change is recorded as DocFlow support
 * (Section 7.15.1, "Acting-as, not impersonation"; D-111) -- the Console's
 * own navigation is already above it.
 */
export function ReviewChrome() {
  const { tenantId } = useReviewScope();
  if (!tenantId) return <AppHeader />;
  return <SupportBanner tenantId={tenantId} />;
}

function SupportBanner({ tenantId }: { tenantId: string }) {
  const [name, setName] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getTenantOverview(tenantId)
      .then(({ tenant }) => {
        if (!cancelled) setName(tenant.name);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  return (
    <div
      data-testid="support-banner"
      className="border-b border-amber-300 bg-amber-50 px-5 py-2 text-sm text-amber-900"
    >
      <div className="mx-auto flex max-w-[110rem] flex-wrap items-center justify-between gap-2">
        <span>
          Reviewing <strong>{name ?? "this tenant"}</strong>&apos;s orders as <strong>DocFlow support</strong>.
          Every change is recorded under your name and shown to them as DocFlow support.
        </span>
        <Link href={`/admin/tenants/${tenantId}`} className="font-medium underline">
          Back to the tenant
        </Link>
      </div>
    </div>
  );
}
