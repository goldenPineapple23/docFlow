import { apiFetch } from "./api";
import { ReviewApiError, UNEXPECTED, request, type CatalogError } from "./review";

/**
 * The customer's own dashboard and uploads (slice 5.8a, D-128).
 *
 * The dashboard is admin-only at the API; a reviewer gets a catalog entry
 * (`AUTH-003`) explaining why, which the page renders as given (7.16.5).
 */

export type ActivityItem = {
  at: string;
  kind: "approved" | "rejected" | "edited" | "reopened" | "exported" | "released";
  document_id: string | null;
  document_name: string | null;
  po_number: string | null;
  by: string | null;
  by_docflow_support: boolean;
  detail: string | null;
};

export type Home = {
  documents_by_status: Record<string, number>;
  oldest_needs_review_at: string | null;
  received_today: number;
  this_month: {
    arrived: number;
    counted: number;
    approved: number;
    exported: number;
    median_hours_to_approval: number | null;
  };
  allowance: {
    used: number;
    allowance: number | null;
    tier: string | null;
    banner: (CatalogError & { threshold_pct: number }) | null;
  };
  held: {
    total: number;
    groups: { reason: string; count: number; title: string; message: string }[];
  };
  activity: ActivityItem[];
};

export const getHome = () => request<Home>("/home");

/** What happened to one file the person chose. */
export type UploadOutcome =
  | { file: string; ok: true; documentId: string; status: string; duplicateOf: string | null }
  | { file: string; ok: false; held: boolean; error: CatalogError };

/**
 * Uploads one file through the one upload endpoint every other intake path uses
 * (Section 10: no second upload handler). One request per file, so a refusal
 * names the file it belongs to and the rest still go.
 */
export async function uploadOne(file: File): Promise<UploadOutcome> {
  const body = new FormData();
  body.append("file", file);

  let response: Response;
  try {
    response = await apiFetch("/documents/upload", { method: "POST", body });
  } catch {
    return { file: file.name, ok: false, held: false, error: UNEXPECTED };
  }

  let payload: Record<string, unknown> = {};
  try {
    payload = (await response.json()) as Record<string, unknown>;
  } catch {
    // Not JSON; the fallback below covers it.
  }

  if (!response.ok) {
    const detail = payload?.detail as CatalogError | undefined;
    const catalog =
      detail && typeof detail === "object" && detail.code && detail.title && detail.action
        ? detail
        : UNEXPECTED;
    return { file: file.name, ok: false, held: false, error: catalog };
  }

  // Accepted, but held rather than processed: an abuse ceiling is tripped
  // (7.16.2). Stored, never discarded -- the catalog says so in its own words.
  const held = payload.held as CatalogError | undefined;
  if (held) return { file: file.name, ok: false, held: true, error: held };

  return {
    file: file.name,
    ok: true,
    documentId: String(payload.document_id ?? ""),
    status: String(payload.status ?? "pending"),
    duplicateOf: (payload.possible_duplicate_of as string | undefined) ?? null,
  };
}

export { ReviewApiError };
