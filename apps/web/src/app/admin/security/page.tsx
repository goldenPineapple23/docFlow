"use client";

import { useEffect, useState } from "react";
import { AuthenticatorSetup } from "@/components/admin/AuthenticatorSetup";
import { currentLevel } from "@/lib/mfa";

/**
 * The founder's authenticator (DECISIONS.md D-151, D-177). Reachable while
 * enforcement is off, so the founder can enrol -- and pass a first challenge --
 * before CONSOLE_MFA_ENFORCED is switched on (RUNBOOK section 4). Once it is
 * on, the Console gate shows the same component to a session without a code.
 */
export default function SecurityPage() {
  const [level, setLevel] = useState<string | null | undefined>(undefined);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    currentLevel().then((l) => {
      if (!cancelled) setLevel(l);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <h1 className="text-xl font-semibold">Your authenticator</h1>
      <p className="mt-1 text-sm text-gray-600">
        The Console changes and deletes customer accounts, so it needs a code from an
        authenticator app as well as your password. Hard delete, clearing held documents,
        cancelling, rotating an intake address, merging buyers, going live and plan changes also
        ask for a fresh code if yours is more than five minutes old.
      </p>
      <p className="mt-2 text-sm text-gray-600">
        Lost your phone? Follow RUNBOOK section 4.3 to remove the lost authenticator and set up a
        new one. A second authenticator (for example in a password manager) avoids needing that.
      </p>
      <div className="mt-6 max-w-lg rounded border border-gray-200 p-4">
        {level === undefined ? (
          <p className="text-sm text-gray-500">Checking this sign-in…</p>
        ) : level !== "aal2" ? (
          <AuthenticatorSetup onDone={() => setLevel("aal2")} />
        ) : (
          <>
            <p data-testid="mfa-done" className="text-sm text-green-800">
              This sign-in is confirmed with your authenticator.
            </p>
            {added ? (
              <p data-testid="mfa-added" className="mt-3 text-sm text-green-800">
                Second authenticator added. Either one now works.
              </p>
            ) : adding ? (
              <div className="mt-4">
                <AuthenticatorSetup
                  addAnother
                  onDone={() => {
                    setAdding(false);
                    setAdded(true);
                  }}
                />
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setAdding(true)}
                className="mt-4 rounded border border-gray-300 px-3 py-1.5 text-sm"
              >
                Add a second authenticator (backup)
              </button>
            )}
          </>
        )}
      </div>
    </>
  );
}
