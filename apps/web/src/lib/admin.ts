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
  open_merge_candidates: number;
  learned_rules: number;
};

export type TenantRow = {
  id: string;
  name: string;
  status: string;
  onboarding_status: string;
  created_at: string;
  went_live_at: string | null;
  stripe_subscription_status: string | null;
  tier_code: string | null;
  tier_name: string | null;
  tier_monthly_price: string | null;
  tier_document_allowance: number | null;
  documents_this_month: number;
  last_document_at: string | null;
  ai_cost_this_month: string;
  needs_review: number;
  needs_review_oldest_days: string | null;
  mean_confidence_30: string | null;
  mean_confidence_7: string | null;
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
  trial_period_days: number;
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

// ── Operator screens: buyer merge and learned rules (slice 5.4; D-119) ──────

export type MergeSide = {
  id: string;
  name: string;
  contact_email: string | null;
  external_account_number: string | null;
  created_at: string;
  documents: number;
  rules: number;
};

export type MergeCandidate = {
  id: string;
  similarity_score: string;
  created_at: string;
  detected_from_document_id: string | null;
  /** The older (existing) customer first, then the newly created one. */
  buyers: [MergeSide, MergeSide];
};

export type MergeHistoryRow = {
  id: string;
  created_at: string;
  kept_name: string;
  merged_name: string;
  documents_moved: number;
  rules_moved: number;
  fields_filled: string[];
  merged_by_email: string | null;
  by_docflow_support: boolean;
};

export const getBuyerMerges = (tenantId: string) =>
  request<{ candidates: MergeCandidate[]; history: MergeHistoryRow[] }>(`/admin/tenants/${tenantId}/buyer-merges`);

export const mergeBuyers = (tenantId: string, candidateId: string, keepBuyerId: string) =>
  request<{ merge_id: string; documents_moved: number; rules_moved: number; fields_filled: string[] }>(
    `/admin/tenants/${tenantId}/buyer-merges/${candidateId}/merge`,
    { method: "POST", body: JSON.stringify({ keep_buyer_id: keepBuyerId }) },
  );

export const dismissBuyerMerge = (tenantId: string, candidateId: string) =>
  request<{ ok: boolean }>(`/admin/tenants/${tenantId}/buyer-merges/${candidateId}/dismiss`, { method: "POST" });

export type LearnedRule = {
  id: string;
  rule_type: "sku_mapping" | "buyer_alias" | "uom_alias" | "field_hint";
  match_key: string;
  match_value: Record<string, unknown>;
  status: "active" | "disabled";
  times_applied: number;
  created_at: string;
  updated_at: string;
  buyer_id: string | null;
  buyer_name: string | null;
  confirmed_by_email: string | null;
  by_docflow_support: boolean;
  source_document_id: string | null;
  source_po_number: string | null;
  item_sku: string | null;
  item_description: string | null;
  item_retired: boolean;
};

export const getRules = (tenantId: string) => request<{ rules: LearnedRule[] }>(`/admin/tenants/${tenantId}/rules`);

export const changeRule = (tenantId: string, ruleId: string, action: "disable" | "enable" | "delete") =>
  request<{ change: { before: string; after: string } }>(`/admin/tenants/${tenantId}/rules/${ruleId}/${action}`, {
    method: "POST",
  });

// ── Per-tenant field schema (slice 5.4 part 2; D-120) ──────────────────────

export type FieldSetting = {
  name: string;
  label: string;
  level: "header" | "line";
  state: "required" | "optional" | "hidden";
  default: "required" | "optional" | "hidden";
  locked: boolean;
};

export type FieldSchemaVersion = {
  version: number;
  fields: Record<string, Record<string, string>>;
  note: string | null;
  created_at: string;
  created_by_email: string | null;
  acting_as_tenant_id: string | null;
};

export const getFieldSchema = (tenantId: string) =>
  request<{ schema: { version: number; fields: FieldSetting[] }; history: FieldSchemaVersion[] }>(
    `/admin/tenants/${tenantId}/field-schema`,
  );

export const saveFieldSchema = (
  tenantId: string,
  body: {
    fields: { header?: Record<string, string>; line?: Record<string, string> };
    note: string | null;
    apply_to_open_documents: boolean;
  },
) =>
  request<{ schema: { version: number; fields: FieldSetting[] }; rechecked_documents: number }>(
    `/admin/tenants/${tenantId}/field-schema`,
    { method: "PUT", body: JSON.stringify(body) },
  );


// ── Dashboard (slice 5.5; D-121) ───────────────────────────────────────────

/** Every KPI the nightly rollup can answer. Money and shares are strings. */
export type Kpis = {
  documents_received: number;
  documents_failed: number;
  documents_approved: number;
  documents_exported: number;
  zero_edit_approvals: number;
  edited_actions: number;
  line_items: number;
  matched_lines: number;
  learned_rule_lines: number;
  review_within_target: number;
  review_sessions: number;
  review_sessions_excluded: number;
  est_cost_usd: string;
  mean_confidence: string | null;
  review_within_target_share: string | null;
  zero_edit_share: string | null;
  mapping_reuse_share: string | null;
  corrections_per_100_lines: string | null;
  median_hours_to_approval: string | null;
  mean_cost_per_document: string | null;
  p95_cost_per_document: string | null;
};

export type Dashboard = {
  days: number;
  kpis: Kpis;
  previous: Kpis;
  health: {
    pending: number;
    processing: number;
    oldest_waiting_minutes: string | null;
    documents_today: number;
    needs_review: number;
    model_calls_hour: number;
    model_failures_hour: number;
    spend_today: string;
    spend_yesterday: string;
    last_document_processed_at: string | null;
  };
  money: {
    mrr: string;
    live_tenants: number;
    live_60: number;
    retained_60: number;
    live_90: number;
    retained_90: number;
    ai_cost_this_month: string;
  };
  rollup: {
    id: string;
    started_at: string;
    finished_at: string | null;
    trigger: string;
    tenants: number;
    rows_written: number;
    ok: boolean | null;
    error: string | null;
  } | null;
  rollup_stale_hours: number;
  rollup_is_stale: boolean;
  queues: { interactive: number | null; bulk: number | null };
  worker: string | null;
};

export const getDashboard = (days = 30) => request<Dashboard>(`/admin/dashboard?days=${days}`);

export const getTenantMetrics = (tenantId: string, days = 30) =>
  request<{ days: number; kpis: Kpis }>(`/admin/tenants/${tenantId}/metrics?days=${days}`);

export const recomputeRollup = (days = 2) =>
  request<{ queued: boolean; days: number }>("/admin/rollup/recompute", {
    method: "POST",
    body: JSON.stringify({ days }),
  });

// ── Lifecycle actions (Section 7.14 / 7.15.4; D-123) ────────────────────────

export type CancellationReason = "customer_requested" | "non_payment" | "for_cause";

export type LifecycleStatus = {
  status: "active" | "cancelling" | "suspended" | "pending_deletion" | "deleted";
  cancellation_reason: CancellationReason | null;
  cancellation_effective_at: string | null;
  deletion_scheduled_at: string | null;
  stripe_subscription_status: string | null;
  stripe_current_period_end: string | null;
  first_past_due_at: string | null;
};

export const getLifecycle = (tenantId: string) =>
  request<LifecycleStatus>(`/admin/tenants/${tenantId}/lifecycle`);

export type CancelPreview = { effective_at: string; rule: string; flagged: boolean };

export const previewCancel = (tenantId: string, reason: CancellationReason) =>
  request<CancelPreview>(`/admin/tenants/${tenantId}/cancel/preview?reason=${reason}`);

export const cancelTenant = (
  tenantId: string,
  body: { reason: CancellationReason; note?: string; override_effective_at?: string },
) =>
  request<{ status: string; cancellation_effective_at: string; rule: string; flagged: boolean }>(
    `/admin/tenants/${tenantId}/cancel`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const reactivateTenant = (tenantId: string) =>
  request<{ status: string }>(`/admin/tenants/${tenantId}/reactivate`, { method: "POST" });

export type WindDownTenant = {
  id: string;
  name: string;
  status: string;
  cancellation_reason: CancellationReason | null;
  cancellation_effective_at: string | null;
  deletion_scheduled_at: string;
  days_remaining: number;
};

export const getWindDownQueue = () =>
  request<{ tenants: WindDownTenant[] }>("/admin/lifecycle/wind-down");

export type ReadyToDeleteTenant = {
  id: string;
  name: string;
  cancellation_reason: CancellationReason | null;
  deletion_scheduled_at: string;
};

export const getReadyToDeleteQueue = () =>
  request<{ tenants: ReadyToDeleteTenant[] }>("/admin/lifecycle/ready-to-delete");

export const deleteTenant = (tenantId: string, body: { confirm_name: string; reason: string }) =>
  request<{ status: string }>(`/admin/tenants/${tenantId}/delete`, {
    method: "POST",
    body: JSON.stringify(body),
  });


// ── Allowances, quarantine, intake address (slice 5.7, D-126) ───────────────

export type QuarantineReason =
  | "abuse_ceiling"
  | "cost_breaker"
  | "attachment_cap"
  | "auth_fail"
  | "unknown_sender_velocity"
  | "sender_not_allowed"
  | "manual";

export type HeldDocumentRow = {
  id: string;
  original_filename: string;
  content_sha256: string;
  sender_email: string | null;
  source: string;
  quarantine_reason: QuarantineReason;
  quarantined_at: string;
  created_at: string;
  subject: string | null;
  spf_result: string | null;
  dkim_result: string | null;
  dmarc_result: string | null;
  file_type: string | null;
  reason_label: string;
};

export type QuarantineView = {
  usage: { used: number; allowance: number | null; tier: string | null; month: string };
  sender_settings: { strict_sender_mode: boolean; sender_allowlist: string[] };
  expired_held: number;
  limits: {
    monthly_ceiling: number | null;
    monthly_ceiling_reached: boolean;
    daily_cost_ceiling_usd: string;
    spend_today_usd: string;
    daily_cost_ceiling_reached: boolean;
  };
  groups: { reason: QuarantineReason; label: string; count: number; title: string; message: string }[];
  documents: HeldDocumentRow[];
};

export const getQuarantine = (tenantId: string) =>
  request<QuarantineView>(`/admin/tenants/${tenantId}/quarantine`);

export const releaseQuarantine = (tenantId: string, documentIds: string[]) =>
  request<{ released: string[]; skipped: string[] }>(`/admin/tenants/${tenantId}/quarantine/release`, {
    method: "POST",
    body: JSON.stringify({ document_ids: documentIds }),
  });

export const clearQuarantine = (tenantId: string, documentIds: string[], confirmName: string) =>
  request<{ cleared: string[] }>(`/admin/tenants/${tenantId}/quarantine/clear`, {
    method: "POST",
    body: JSON.stringify({ document_ids: documentIds, confirm_name: confirmName }),
  });

export const rotateIntakeAddress = (tenantId: string) =>
  request<{ address: string; grace_ends_at: string }>(`/admin/tenants/${tenantId}/intake-address/rotate`, {
    method: "POST",
  });

export const putSenderSettings = (tenantId: string, strict: boolean, allowlist: string[]) =>
  request<{ strict_sender_mode: boolean; sender_allowlist: string[] }>(
    `/admin/tenants/${tenantId}/sender-settings`,
    { method: "PUT", body: JSON.stringify({ strict_sender_mode: strict, sender_allowlist: allowlist }) },
  );
