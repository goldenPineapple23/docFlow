import { apiFetch } from "./api";

/**
 * The review API, typed (CLAUDE.md Section 7.3, Phase 3 slice 3).
 *
 * Every money value is a `string` here and stays one all the way to the
 * input element. Section 7.1 says no float ever touches money, and
 * JavaScript has no decimal type -- parsing "570.00" into a Number to
 * render it would be exactly the mistake that section exists to prevent.
 * These values are displayed, edited and sent back as text.
 */

export type CatalogError = {
  code: string;
  title: string;
  message: string;
  action: string;
  detail?: Record<string, unknown>;
};

export type QueueDocument = {
  id: string;
  original_filename: string;
  status: string;
  source: string;
  created_at: string | null;
  approved_at: string | null;
  review_started: boolean;
  po_number: string | null;
  buyer_name: string | null;
  order_total: string | null;
  currency: string | null;
  overall_confidence: string | null;
  open_warnings: number;
  is_test_batch: boolean;
  injection_suspected: boolean | null;
  is_possible_duplicate: boolean;
  is_possible_change_order: boolean;
};

export type DocumentHeader = {
  po_number: string | null;
  order_date: string | null;
  requested_delivery_date: string | null;
  buyer_name: string | null;
  buyer_contact_email: string | null;
  ship_to_address: string | null;
  payment_terms: string | null;
  order_total: string | null;
  currency: string | null;
  notes: string | null;
  currency_inferred: boolean | null;
  confidence: Record<string, number>;
  provenance: Record<string, string>;
};

export type DocumentLine = {
  id: string;
  line_number: number;
  sku: string | null;
  description: string | null;
  unit: string | null;
  quantity: string | null;
  unit_price: string | null;
  line_total: string | null;
  confidence: string | null;
  matched_item_id: string | null;
  match_method: string | null;
  match_score: string | null;
  matched_uom: string | null;
  uom_mismatch: boolean;
  match_candidates: Array<{ item_id?: string; sku?: string; description?: string; score?: string }>;
  provenance: Record<string, string>;
};

export type DocumentWarning = {
  id: string;
  code: string;
  severity: string;
  field_name: string | null;
  line_number: number | null;
  document_line_id: string | null;
  detail: Record<string, string>;
  status: string;
  acknowledged_at: string | null;
};

export type FieldChange = {
  field: string;
  before: string | null;
  after: string | null;
  line_number?: number;
};

export type TrailEntry = {
  id: string;
  action: "edited" | "approved" | "rejected" | "reopened";
  user_id: string;
  by_docflow_support: boolean;
  changes: FieldChange[];
  warning_acknowledgements: Array<{ warning_id: string; code: string; text: string; note: string | null }>;
  note: string | null;
  created_at: string | null;
};

export type DocumentDetail = {
  document: {
    id: string;
    original_filename: string;
    status: string;
    source: string;
    created_at: string | null;
    approved_at: string | null;
    approved_by: string | null;
    approved_snapshot_hash: string | null;
    overall_confidence: string | null;
    injection_suspected: boolean | null;
    is_test_batch: boolean;
    is_possible_duplicate: boolean;
    duplicate_of_document_id: string | null;
    is_possible_change_order: boolean;
    change_order_of_document_id: string | null;
  };
  header: DocumentHeader;
  lines: DocumentLine[];
  warnings: DocumentWarning[];
  trail: TrailEntry[];
  version: string;
  can_edit: boolean;
};

export type CatalogItem = {
  id: string;
  sku: string;
  description: string | null;
  unit_of_measure: string | null;
};

/**
 * A failure carrying its catalog code.
 *
 * The UI never writes its own sentence for a backend failure -- it renders
 * `title`, `message` and `action` from the catalog (Section 7.16.5). The one
 * string this module owns is the fallback below, for a response that isn't
 * a catalog entry at all (a network failure, a 500, a proxy error page).
 */
export class ReviewApiError extends Error {
  readonly catalog: CatalogError;
  readonly status: number;

  constructor(catalog: CatalogError, status: number) {
    super(`${catalog.code}: ${catalog.title}`);
    this.catalog = catalog;
    this.status = status;
  }
}

const UNEXPECTED: CatalogError = {
  code: "APP-000",
  title: "We couldn't reach DocFlow",
  message: "The request didn't get through, so nothing was changed.",
  action: "Check your connection and try again. If it keeps happening, DocFlow has been alerted.",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch {
    throw new ReviewApiError(UNEXPECTED, 0);
  }

  if (!response.ok) {
    let catalog = UNEXPECTED;
    try {
      const body = await response.json();
      const detail = body?.detail;
      // A catalog-coded failure has all four fields. Anything else (a
      // FastAPI validation error, a bare string) falls back rather than
      // being rendered as if it were a catalog entry.
      if (detail && typeof detail === "object" && detail.code && detail.title && detail.action) {
        catalog = detail as CatalogError;
      }
    } catch {
      // Not JSON. Keep the fallback.
    }
    throw new ReviewApiError(catalog, response.status);
  }

  return (await response.json()) as T;
}

export function listDocuments(status?: string): Promise<{ documents: QueueDocument[] }> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/review/documents${query}`);
}

export function getDocument(id: string): Promise<DocumentDetail> {
  return request(`/review/documents/${id}`);
}

export function saveEdits(
  id: string,
  body: {
    header?: Record<string, string | null>;
    lines?: Array<{ line_id: string; fields: Record<string, string | null> }>;
    expected_version: string;
  },
): Promise<{ review_action_id: string | null; version: string }> {
  return request(`/review/documents/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function approveDocument(
  id: string,
  acknowledgements: Array<{ warning_id: string; code: string; text: string; note: string | null }>,
): Promise<{ review_action_id: string; status: string }> {
  return request(`/review/documents/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ acknowledgements }),
  });
}

export function rejectDocument(id: string, note: string): Promise<{ status: string }> {
  return request(`/review/documents/${id}/reject`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

export function searchItems(q: string): Promise<{ items: CatalogItem[] }> {
  return request(`/review/items?q=${encodeURIComponent(q)}`);
}

export function createMapping(
  documentId: string,
  lineId: string,
  itemId: string,
): Promise<{ learned_rule_id: string | null }> {
  return request(`/review/documents/${documentId}/mapping`, {
    method: "POST",
    body: JSON.stringify({ line_id: lineId, item_id: itemId }),
  });
}

export function originalDocumentUrl(id: string): Promise<{ url: string; expires_at: number }> {
  return request(`/review/documents/${id}/original`);
}
