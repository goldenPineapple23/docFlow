import { apiFetch } from "./api";
import { ReviewApiError, request, type CatalogError } from "./review";

/**
 * The founder Console API, typed (CLAUDE.md Section 7.15).
 *
 * Every route answers 404 to anyone who is not a platform admin (7.15.1);
 * the Console layout redirects before any of these are called. Failures are
 * catalog entries (CON-0xx), rendered as given -- the same rule as the tenant
 * surface (7.16.5).
 */

export type Tier = {
  id: string;
  code: "starter" | "growth" | "scale";
  version: number;
  name: string;
  monthly_price: string;
  promo_monthly_price: string | null;
  promo_days: number | null;
  document_allowance: number;
};

export type IntakeSummary = {
  id: string;
  prospect_name: string;
  contact_email: string | null;
  received_at: string;
  source: string;
  linked_tenant_id: string | null;
  linked_tenant_name: string | null;
  file_count: number;
};

export type IntakeFile = {
  id: string;
  original_filename: string;
  sha256: string;
  byte_size: number;
  detected_type: string;
  created_at: string;
};

export type Intake = IntakeSummary & { notes: string | null; linked_at: string | null; files: IntakeFile[] };

export type TenantOverview = {
  id: string;
  name: string;
  primary_currency: string;
  timezone: string;
  status: string;
  onboarding_status: string;
  went_live_at: string | null;
  invite_sent_at: string | null;
  intake_address_active: boolean;
  stripe_customer_id: string | null;
  stripe_subscription_status: string | null;
  created_at: string;
  onboarding_intake_id: string | null;
  tier_code: string | null;
  tier_name: string | null;
  tier_version: number | null;
  tier_monthly_price: string | null;
  tier_document_allowance: number | null;
  owner: { id: string; email: string; has_login: boolean; invite_sent_at: string | null } | null;
  intake_address: string | null;
};

export type TenantRow = {
  id: string;
  name: string;
  status: string;
  onboarding_status: string;
  created_at: string;
  went_live_at: string | null;
};

export type FounderAlert = {
  id: string;
  type: string;
  severity: "info" | "warning" | "high" | "critical";
  tenant_id: string | null;
  tenant_name: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type OutboxEmail = {
  id: string;
  tenant_id: string | null;
  tenant_name: string | null;
  to_address: string;
  template: string;
  subject: string;
  body_text: string;
  status: "queued" | "held" | "sent" | "failed";
  error: string | null;
  created_at: string;
  sent_at: string | null;
};

export const listTiers = () => request<{ tiers: Tier[] }>("/admin/tiers");
export const listIntakes = () => request<{ intakes: IntakeSummary[] }>("/admin/intakes");
export const getIntake = (id: string) => request<{ intake: Intake }>(`/admin/intakes/${id}`);

export function createIntake(body: {
  prospect_name: string;
  contact_email: string | null;
  source: "email" | "other";
  notes: string | null;
}) {
  return request<{ intake_id: string }>("/admin/intakes", { method: "POST", body: JSON.stringify(body) });
}

/**
 * Multipart, so it cannot go through `request` (which sends JSON). Same
 * catalog-error handling.
 */
export async function uploadIntakeFile(intakeId: string, file: File): Promise<{ file_id: string }> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiFetch(`/admin/intakes/${intakeId}/files`, { method: "POST", body: form });
  if (!response.ok) {
    let catalog: CatalogError = {
      code: "APP-000",
      title: "We couldn't reach DocFlow",
      message: "The upload didn't get through, so nothing was stored.",
      action: "Check your connection and try again.",
    };
    try {
      const detail = (await response.json())?.detail;
      if (detail?.code && detail?.title && detail?.action) catalog = detail;
    } catch {
      // Not JSON; keep the fallback.
    }
    throw new ReviewApiError(catalog, response.status);
  }
  return response.json();
}

export function createTenant(body: {
  name: string;
  owner_email: string;
  primary_currency: string;
  timezone: string;
  tier: string;
  intake_id: string | null;
}) {
  return request<{ tenant_id: string; owner_user_id: string }>("/admin/tenants/new", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const listTenants = () => request<TenantRow[]>("/admin/tenants");
export const getTenantOverview = (id: string) =>
  request<{ tenant: TenantOverview }>(`/admin/tenants/${id}/overview`);
export const sendInvite = (id: string) =>
  request<{ email_outbox_id: string; held: boolean }>(`/admin/tenants/${id}/invite`, { method: "POST" });

export const listAlerts = () => request<{ alerts: FounderAlert[] }>("/admin/alerts");
export const acknowledgeAlert = (id: string) =>
  request<{ acknowledged: boolean }>(`/admin/alerts/${id}/acknowledge`, { method: "POST" });

export const listOutbox = (tenantId?: string) =>
  request<{ emails: OutboxEmail[] }>(`/admin/outbox${tenantId ? `?tenant_id=${tenantId}` : ""}`);
