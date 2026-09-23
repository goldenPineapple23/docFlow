import { request } from "./review";

/**
 * The tenant's view of allowances and held documents (CLAUDE.md Section
 * 7.16.1, 7.16.4; D-126). Every word a person reads here -- the banner, the
 * reason a document is held -- is a catalog entry the API hands over
 * (7.16.5); nothing on this side writes its own wording.
 */

export type CatalogText = { code: string; title: string; message: string; action: string };

export type AllowanceBanner = CatalogText & { threshold_pct: number };

export type Allowance = {
  used: number;
  allowance: number | null;
  tier: string | null;
  month: string;
  banner: AllowanceBanner | null;
};

export type HeldGroup = CatalogText & { reason: string; count: number; can_release: boolean };

export type HeldDocument = {
  id: string;
  filename: string;
  sender_email: string | null;
  reason: string;
  received_at: string;
  can_release: boolean;
};

export type Held = { total: number; groups: HeldGroup[]; documents: HeldDocument[] };

export type IgnoredMail = CatalogText & {
  id: string;
  sender_email: string | null;
  subject: string | null;
  filename: string | null;
  received_at: string;
};

export const getAllowance = () => request<Allowance>("/allowance");

export const getHeld = () => request<Held>("/held");

export const releaseHeld = (documentIds: string[]) =>
  request<{ released: string[]; skipped: string[] }>("/held/release", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_ids: documentIds }),
  });

export const getIgnoredMail = () => request<{ mail: IgnoredMail[] }>("/ignored-mail");
