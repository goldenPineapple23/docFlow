import { request, type CatalogError } from "./review";

/**
 * The owner's Billing page (card billing, founder 2026-09-29; D-181).
 *
 * Owners and admins only at the API. Card details never reach DocFlow: both
 * actions return a Stripe-hosted page to go to. A Checkout link expires after
 * 24 hours, so each click asks the API for a fresh one.
 */

export type Billing = {
  /** How the live subscription collects; null before go-live. */
  billing_method: "card" | "invoice" | null;
  card_on_file: boolean;
  subscription_status: string | null;
  /** A standard customer's setup fee, charged when the card is added (D2). */
  setup_fee_due_now: string | null;
  setup_fee_paid: boolean;
  /** BIL-006 before the date the account may be paused from, BIL-009 after. */
  banner: CatalogError | null;
};

export const getBilling = () => request<Billing>("/billing");

export const openCardPage = () =>
  request<{ url: string }>("/billing/card-page", { method: "POST" });

export const openCardUpdatePage = () =>
  request<{ url: string }>("/billing/card-update-page", { method: "POST" });
