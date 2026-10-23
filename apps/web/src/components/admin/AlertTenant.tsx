import Link from "next/link";
import type { FounderAlert } from "@/lib/admin";

/**
 * Who an alert on the Console home is about: the tenant, linked to its page,
 * or "No tenant". A staged intake past its retention period has no tenant by
 * definition (Section 7.15.2 Step 1), so its "No tenant" links to the intake
 * list, where it is deleted or linked (Stage 5, M7; D-214).
 */
export function AlertTenant({ alert }: { alert: FounderAlert }) {
  if (alert.tenant_id) {
    return (
      <Link href={`/admin/tenants/${alert.tenant_id}`} className="text-blue-700 hover:underline">
        {alert.tenant_name ?? alert.tenant_id}
      </Link>
    );
  }
  if (alert.type === "staging_ttl_elapsed") {
    return (
      <Link href="/admin/intakes" className="text-blue-700 hover:underline">
        No tenant
      </Link>
    );
  }
  return <>No tenant</>;
}
