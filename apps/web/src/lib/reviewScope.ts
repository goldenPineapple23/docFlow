import { usePathname } from "next/navigation";

/**
 * Which review the screen is in: a tenant user's own (`/review/...`), or the
 * founder acting as DocFlow support in one tenant from the Console
 * (`/admin/tenants/{id}/review/...`; CLAUDE.md Section 7.15.1, D-111).
 *
 * The same review pages and components serve both (Section 10: no second
 * review component for the Console). Only two things differ, both derived
 * from the page's own address: the API prefix the calls go to, and where
 * the screen's links point. The server's acting-as gate, not this, is what
 * decides who may use the Console's routes.
 */

const CONSOLE_REVIEW = /^\/admin\/tenants\/([0-9a-fA-F-]{36})\/review(?:\/|$)/;

export function consoleTenantFromPath(pathname: string | null | undefined): string | null {
  return pathname?.match(CONSOLE_REVIEW)?.[1] ?? null;
}

/** The API prefix for review and export calls, read at call time. */
export function reviewApiBase(): string {
  const tenantId = typeof window === "undefined" ? null : consoleTenantFromPath(window.location.pathname);
  return tenantId ? `/admin/tenants/${tenantId}/act/review` : "/review";
}

export function useReviewScope(): { tenantId: string | null; href: (sub?: string) => string } {
  const tenantId = consoleTenantFromPath(usePathname());
  const base = tenantId ? `/admin/tenants/${tenantId}/review` : "/review";
  return { tenantId, href: (sub = "") => `${base}${sub}` };
}
