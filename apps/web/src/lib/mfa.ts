import { supabase } from "./supabase";

/**
 * The Console's authenticator (TOTP) calls, straight to Supabase Auth from
 * the signed-in browser (DECISIONS.md D-151, D-177). Nothing here goes
 * through our API: enrolment and challenges are Supabase's, and the API only
 * reads the result off the session token (`aal`, and the `amr` entry for
 * `totp`).
 *
 * Every failure is reported as `false` or `null`, never as Supabase's own
 * message: the page shows catalog entry AUTH-008 instead (Section 7.16.5).
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

/** Start enrolling an authenticator. Unverified leftovers are removed first,
 * so an abandoned attempt never blocks a new one. */
export async function startEnrolment(): Promise<Enrolment | null> {
  const listed = await supabase.auth.mfa.listFactors();
  for (const factor of listed.data?.all ?? []) {
    if (factor.factor_type === "totp" && factor.status === "unverified") {
      await supabase.auth.mfa.unenroll({ factorId: factor.id });
    }
  }
  const { data, error } = await supabase.auth.mfa.enroll({
    factorType: "totp",
    friendlyName: `DocFlow Console ${new Date().toISOString().slice(0, 10)}`,
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
