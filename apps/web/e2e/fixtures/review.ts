/**
 * Review-screen test data shared by the browser specs (a spec file can't be
 * imported by another without registering its tests twice).
 *
 * All data is fictional (CLAUDE.md Section 0 rule 4).
 */

export const DOCUMENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
export const LINE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
export const WARNING_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";

export function detail(overrides: Record<string, unknown> = {}) {
  return {
    document: {
      id: DOCUMENT_ID,
      original_filename: "po.pdf",
      status: "needs_review",
      source: "email",
      created_at: "2026-09-16T09:00:00Z",
      approved_at: null,
      approved_by: null,
      approved_snapshot_hash: null,
      overall_confidence: "0.91",
      injection_suspected: false,
      is_test_batch: false,
      is_possible_duplicate: false,
      duplicate_of_document_id: null,
      is_possible_change_order: false,
      change_order_of_document_id: null,
      examples_used: 0,
    },
    header: {
      po_number: "BCH-2291",
      order_date: "2025-05-28",
      requested_delivery_date: null,
      buyer_name: "Bella's Test Coffee House",
      buyer_contact_email: null,
      ship_to_address: null,
      payment_terms: null,
      order_total: "570.00",
      currency: "USD",
      notes: null,
      currency_inferred: false,
      confidence: { po_number: 0.98, order_total: 0.62 },
      provenance: {},
    },
    lines: [
      {
        id: LINE_ID,
        line_number: 1,
        sku: "CF-1001",
        description: "Colombian Whole Bean 5lb",
        unit: "CS",
        quantity: "12.0000",
        unit_price: "47.5000",
        line_total: "570.00",
        confidence: "0.97",
        matched_item_id: null,
        match_method: null,
        match_score: null,
        matched_uom: null,
        uom_mismatch: false,
        match_candidates: [],
        provenance: {},
      },
    ],
    warnings: [],
    trail: [],
    version: "version-1",
    can_edit: true,
    ...overrides,
  };
}
