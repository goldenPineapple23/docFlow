import { supabase } from "./supabase";

/**
 * The Console's authenticator (TOTP) calls, straight to Supabase Auth from
 * the signed-in browser (DECISIONS.md D-151, D-177). Nothing here goes
 * through our API: enrolment and challenges are Supabase's, and the API only
 * reads the result off the session token (`aal`, and the `amr` entry for
 * `totp`).
 *
 * Every failure is reported as `false` or `null`, never as Supabase's own
 * message: the page shows a catalog entry instead (Section 7.16.5) -- AUTH-008
 * for a refused code, AUTH-009 for an enrolment that didn't start.
 */

export type Enrolment = {
  factorId: string;
  /** An SVG data URI for the QR code; rendered in an <img>, never as HTML. */
  qrCode: string;
  /** The same secret as text, for typing into an app by hand. */
  secret: string;
};

/** "aal2" once this sign-in has passed a challenge; "aal1" before; null if unknown. */
export async function currentLevel(): Promise<string | null> {
  const { data, error } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  return error || !data ? null : data.currentLevel;
}

/** The ids of this account's verified authenticators (a backup makes two). */
export async function verifiedFactorIds(): Promise<string[]> {
  const { data, error } = await supabase.auth.mfa.listFactors();
  if (error || !data) return [];
  return data.totp.map((factor) => factor.id);
}

/** The id of this account's first verified authenticator, or null if none. */
export async function verifiedFactorId(): Promise<string | null> {
  return (await verifiedFactorIds())[0] ?? null;
}

/** Confirm a code against whichever verified authenticator produced it. */
export async function confirmWithAnyFactor(code: string): Promise<boolean> {
  for (const factorId of await verifiedFactorIds()) {
    if (await confirmCode(factorId, code)) return true;
  }
  return false;
}

/**
 * A name for a new authenticator that none of the account's others has.
 * Supabase refuses a second factor with the same name (422), which is how the
 * first backup authenticator failed on the day its original was enrolled.
 */
export function uniqueFactorName(taken: string[], today: Date = new Date()): string {
  const base = `DocFlow Console ${today.toISOString().slice(0, 10)}`;
  const names = new Set(taken);
  if (!names.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base} (${n})`;
    if (!names.has(candidate)) return candidate;
  }
}

/** Start enrolling an authenticator. Unverified leftovers are removed first,
 * so an abandoned attempt never blocks a new one. */
export async function startEnrolment(): Promise<Enrolment | null> {
  const listed = await supabase.auth.mfa.listFactors();
  const kept: string[] = [];
  for (const factor of listed.data?.all ?? []) {
    if (factor.factor_type === "totp" && factor.status === "unverified") {
      await supabase.auth.mfa.unenroll({ factorId: factor.id });
    } else if (factor.friendly_name) {
      kept.push(factor.friendly_name);
    }
  }
  const { data, error } = await supabase.auth.mfa.enroll({
    factorType: "totp",
    friendlyName: uniqueFactorName(kept),
  });
  if (error || !data) return null;
  return { factorId: data.id, qrCode: data.totp.qr_code, secret: data.totp.secret };
}

/**
 * Pass a challenge with a code from the app. The first success on a new
 * factor verifies it; every success upgrades the session to aal2 and stamps a
 * fresh challenge time on the token. Returns whether the code was accepted.
 */
export async function confirmCode(factorId: string, code: string): Promise<boolean> {
  const cleaned = code.replace(/\s+/g, "");
  if (!/^\d{6}$/.test(cleaned)) return false;
  const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId, code: cleaned });
  return !error;
}
