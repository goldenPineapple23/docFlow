import { apiFetch } from "./api";
import { ReviewApiError, UNEXPECTED, request, type CatalogError } from "./review";

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

/** A setup-fee option from docflow-pricing.docx, from the presets table (D-117). */
export type SetupFeePreset = {
  id: string;
  code: "founding" | "standard" | "complex" | "waived" | "custom";
  version: number;
  name: string;
  description: string;
  default_amount: string | null;
  min_amount: string;
  max_amount: string | null;
  note_required: boolean;
};

export type SetupFeeBilling = "stripe" | "invoiced_manually";

/** The deal agreed before onboarding: recorded at Create tenant, editable until go-live (D-117). */
export type DealTerms = {
  tier: Tier["code"];
  setup_fee_preset: SetupFeePreset["code"];
  setup_fee_amount: string | null;
  setup_fee_billing: SetupFeeBilling;
  setup_fee_note: string | null;
  founding_price: boolean;
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
  tier_promo_monthly_price: string | null;
  tier_promo_days: number | null;
  tier_document_allowance: number | null;
  owner: { id: string; email: string; has_login: boolean; invite_sent_at: string | null } | null;
  intake_address: string | null;
  test_batch_completed_at: string | null;
  setup_fee_amount: string | null;
  setup_fee_billing: SetupFeeBilling | null;
  setup_fee_note: string | null;
  setup_fee_preset: SetupFeePreset["code"] | null;
  setup_fee_preset_name: string | null;
  founding_price: boolean;
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
export const listSetupFeePresets = () => request<{ presets: SetupFeePreset[] }>("/admin/setup-fee-presets");
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
  deal: DealTerms;
}) {
  return request<{ tenant_id: string; owner_user_id: string }>("/admin/tenants/new", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const updateDealTerms = (tenantId: string, body: DealTerms) =>
  request<{ deal: Record<string, unknown> }>(`/admin/tenants/${tenantId}/deal-terms`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

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

// ── Catalog and customer-list import (Steps 4-5; D-108) ─────────────────────

export type ImportKind = "catalog" | "buyers";

export type ImportFinding = CatalogError & {
  severity: "blocker" | "warning" | "info";
  field: string | null;
  count: number;
  rows: number[];
  keys: string[];
};

export type ImportField = { name: string; label: string; required: boolean; max_length: number };

export type ImportPreview = {
  id: string;
  kind: ImportKind;
  status: "parsing" | "parsed" | "failed" | "committed" | "discarded";
  original_filename: string;
  error_code: string | null;
  error?: CatalogError;
  summary: Record<string, unknown> | null;
  fields: ImportField[];
  columns?: string[];
  row_count?: number;
  mapping?: Record<string, number | null>;
  overrides?: Record<string, Record<string, string>>;
  missing_required?: string[];
  report?: ImportFinding[];
  diff?: { insert: number; update: number; reinstate: number; unchanged: number; retire: number } | null;
  retiring?: string[];
  can_commit?: boolean;
  preview?: Array<{
    row_number: number;
    values: Record<string, string | null>;
    /** Fields fixed inline, with what the file itself says. */
    fixed?: Record<string, string>;
  }>;
};

export type ImportSummaryRow = {
  id: string;
  kind: ImportKind;
  status: ImportPreview["status"];
  original_filename: string;
  file_type: string;
  row_count: number | null;
  error_code: string | null;
  summary: Record<string, unknown> | null;
  created_at: string;
  committed_at: string | null;
};

export type TenantIntakeFile = {
  id: string;
  original_filename: string;
  detected_type: string;
  byte_size: number;
  created_at: string;
};

const importsBase = (tenantId: string) => `/admin/tenants/${tenantId}/imports`;

export const listImports = (tenantId: string, kind: ImportKind) =>
  request<{ imports: ImportSummaryRow[] }>(`${importsBase(tenantId)}?kind=${kind}`);

export const listTenantIntakeFiles = (tenantId: string) =>
  request<{ files: TenantIntakeFile[] }>(`/admin/tenants/${tenantId}/intake-files`);

export const getImport = (tenantId: string, importId: string) =>
  request<{ import: ImportPreview }>(`${importsBase(tenantId)}/${importId}`);

export async function uploadImport(tenantId: string, kind: ImportKind, file: File): Promise<{ import_id: string }> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  const response = await apiFetch(importsBase(tenantId), { method: "POST", body: form });
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

export const importFromIntake = (tenantId: string, kind: ImportKind, intakeFileId: string) =>
  request<{ import_id: string }>(`${importsBase(tenantId)}/from-intake`, {
    method: "POST",
    body: JSON.stringify({ kind, intake_file_id: intakeFileId }),
  });

export const setImportMapping = (tenantId: string, importId: string, mapping: Record<string, number | null>) =>
  request<{ ok: boolean }>(`${importsBase(tenantId)}/${importId}/mapping`, {
    method: "PUT",
    body: JSON.stringify({ mapping }),
  });

export const fixImportRow = (tenantId: string, importId: string, rowNumber: number, field: string, value: string) =>
  request<{ ok: boolean }>(`${importsBase(tenantId)}/${importId}/rows/${rowNumber}`, {
    method: "PUT",
    body: JSON.stringify({ field, value }),
  });

export const commitImport = (tenantId: string, importId: string) =>
  request<{ summary: Record<string, unknown> }>(`${importsBase(tenantId)}/${importId}/commit`, { method: "POST" });

export const discardImport = (tenantId: string, importId: string) =>
  request<{ ok: boolean }>(`${importsBase(tenantId)}/${importId}/discard`, { method: "POST" });


// ── Test batch and go-live (Section 7.15.2 Steps 6-9; D-112, D-113) ────────

export type TestBatchDocument = {
  id: string;
  original_filename: string;
  status: string;
  created_at: string;
  approved_at: string | null;
  // Money and confidence arrive as strings (Section 7.1).
  est_cost_usd: string | null;
  overall_confidence: string | null;
  po_number: string | null;
  buyer_name: string | null;
};

export type TestBatchUploadResult = {
  filename: string;
  document_id?: string;
  status?: string;
  possible_duplicate_of?: string;
  error?: CatalogError;
};

export const getTestBatch = (tenantId: string) =>
  request<{ documents: TestBatchDocument[] }>(`/admin/tenants/${tenantId}/test-batch`);

/** Multipart, so not through `request`; the same catalog-error handling. */
export async function uploadTestBatch(
  tenantId: string,
  files: File[],
): Promise<{ results: TestBatchUploadResult[] }> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  let response: Response;
  try {
    response = await apiFetch(`/admin/tenants/${tenantId}/test-batch`, { method: "POST", body: form });
  } catch {
    throw new ReviewApiError(UNEXPECTED, 0);
  }
  if (!response.ok) {
    let catalog: CatalogError = UNEXPECTED;
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

export const runTestBatch = (tenantId: string) =>
  request<{ started: number }>(`/admin/tenants/${tenantId}/test-batch/run`, { method: "POST" });

export const completeTestBatch = (tenantId: string) =>
  request<{ onboarding_status: string }>(`/admin/tenants/${tenantId}/test-batch/complete`, {
    method: "POST",
  });

export type GoLivePlan = {
  tier_name: string;
  monthly_price: string;
  promo_monthly_price: string | null;
  promo_months: number | null;
  document_allowance: number;
  invite_sent: boolean;
  invoice_days_until_due: number;
  setup_fee_amount: string;
  setup_fee_billing: SetupFeeBilling;
  setup_fee_note: string | null;
  setup_fee_preset_name: string | null;
  founding_price: boolean;
};

export const getGoLivePlan = (tenantId: string) =>
  request<GoLivePlan>(`/admin/tenants/${tenantId}/go-live`);

/** Nothing about price is sent: go-live bills the deal recorded on the tenant (D-117). */
export const goLive = (tenantId: string) =>
  request<{ onboarding_status: string }>(`/admin/tenants/${tenantId}/go-live`, { method: "POST" });
