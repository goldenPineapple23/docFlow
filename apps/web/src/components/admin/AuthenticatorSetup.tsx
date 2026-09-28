"use client";

import { useEffect, useState } from "react";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { AUTH_008, AUTH_009 } from "@/lib/catalogMirror";
import { confirmCode, confirmWithAnyFactor, startEnrolment, verifiedFactorId, type Enrolment } from "@/lib/mfa";
import type { CatalogError } from "@/lib/review";

/**
 * Enrol an authenticator app, or enter a code from one (DECISIONS.md D-151,
 * D-177). The Console gate shows this when the session hasn't passed a
 * challenge; /admin/security shows it so the founder can enrol before
 * enforcement is switched on.
 *
 * The QR code is Supabase's SVG, rendered as an <img> from a data URI -- an
 * image, which cannot run script -- never as HTML (Section 7.12).
 */
export function AuthenticatorSetup({
  onDone,
  addAnother = false,
}: {
  onDone: () => void;
  /** Enrol an additional authenticator (a backup) instead of using the existing one.
   * Supabase allows this only from a session that has already passed a challenge. */
  addAnother?: boolean;
}) {
  const [factorId, setFactorId] = useState<string | null | undefined>(addAnother ? null : undefined);
  const [enrolment, setEnrolment] = useState<Enrolment | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CatalogError | null>(null);

  useEffect(() => {
    if (addAnother) return;
    let cancelled = false;
    verifiedFactorId().then((id) => {
      if (!cancelled) setFactorId(id);
    });
    return () => {
      cancelled = true;
    };
  }, [addAnother]);

  async function begin() {
    setBusy(true);
    setError(null);
    // Supabase reports most failures as a value, but a thrown one must not
    // leave the button stuck busy with nothing shown.
    const started = await startEnrolment().catch(() => null);
    setBusy(false);
    if (started === null) {
      setError(AUTH_009);
      return;
    }
    setEnrolment(started);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!enrolment && !factorId) return;
    setBusy(true);
    setError(null);
    // A new authenticator is confirmed against itself; an existing sign-in
    // against whichever of the account's authenticators made the code.
    const accepted = enrolment ? await confirmCode(enrolment.factorId, code) : await confirmWithAnyFactor(code);
    setBusy(false);
    if (!accepted) {
      setError(AUTH_008);
      setCode("");
      return;
    }
    onDone();
  }

  if (factorId === undefined) {
    return <p className="text-sm text-gray-500">Checking your authenticator…</p>;
  }

  const codeForm = (
    <form onSubmit={submit} className="mt-4 flex items-end gap-2">
      <label className="text-sm">
        <span className="block font-medium">Code from your authenticator app</span>
        <input
          data-testid="mfa-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={7}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="mt-1 w-32 rounded border border-gray-300 px-2 py-1 font-mono tracking-widest"
        />
      </label>
      <button
        type="submit"
        disabled={busy || code.trim().length < 6}
        className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
      >
        Confirm
      </button>
    </form>
  );

  return (
    <div data-testid="authenticator-setup">
      {error ? <CatalogErrorBox error={error} /> : null}
      {factorId && !enrolment ? (
        <>
          <p className="text-sm text-gray-700">
            Enter the six-digit code your authenticator app shows for DocFlow.
          </p>
          {codeForm}
        </>
      ) : enrolment ? (
        <>
          <p className="text-sm text-gray-700">
            Scan this with an authenticator app (1Password, Google Authenticator, Authy…), then
            enter the code it shows.
          </p>
          {/* eslint-disable-next-line @next/next/no-img-element -- a data URI, not a remote image */}
          <img
            data-testid="mfa-qr"
            src={enrolment.qrCode}
            alt="QR code for your authenticator app"
            className="mt-3 h-44 w-44 border border-gray-200"
          />
          <p className="mt-2 text-xs text-gray-600">
            Can&apos;t scan it? Type this key into the app instead:{" "}
            <code data-testid="mfa-secret" className="break-all font-mono">
              {enrolment.secret}
            </code>
          </p>
          {codeForm}
        </>
      ) : (
        <>
          <p className="text-sm text-gray-700">
            {addAnother
              ? "A second authenticator -- in a password manager, or on another device -- gets you in if you lose the first."
              : "No authenticator is set up for this account yet. The Console will need one."}
          </p>
          <button
            type="button"
            onClick={begin}
            disabled={busy}
            className="mt-3 rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {addAnother ? "Add a second authenticator" : "Set up an authenticator"}
          </button>
        </>
      )}
    </div>
  );
}
