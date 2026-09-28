"use client";

import { useEffect, useRef, useState } from "react";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { AUTH_008 } from "@/lib/catalogMirror";
import { confirmWithAnyFactor } from "@/lib/mfa";
import type { CatalogError } from "@/lib/review";
import { registerStepUpHandler } from "@/lib/stepUp";

/**
 * The step-up prompt (DECISIONS.md D-151, D-177). Mounted once by the Console
 * layout; `apiFetch` opens it when a destructive action comes back AUTH-007,
 * and retries the action once the code is accepted. Cancelling leaves the
 * action refused -- nothing was done.
 */
export function StepUpDialog() {
  const [refusal, setRefusal] = useState<CatalogError | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [wrong, setWrong] = useState(false);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  useEffect(
    () =>
      registerStepUpHandler(
        (entry) =>
          new Promise<boolean>((resolve) => {
            resolver.current = resolve;
            setCode("");
            setWrong(false);
            setRefusal(entry);
          }),
      ),
    [],
  );

  function finish(ok: boolean) {
    resolver.current?.(ok);
    resolver.current = null;
    setRefusal(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setWrong(false);
    const accepted = await confirmWithAnyFactor(code);
    setBusy(false);
    if (!accepted) {
      setWrong(true);
      setCode("");
      return;
    }
    finish(true);
  }

  if (refusal === null) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="step-up-title"
        data-testid="step-up-dialog"
        className="w-full max-w-md rounded bg-white p-5 shadow-lg"
      >
        <h2 id="step-up-title" className="text-base font-semibold">
          {refusal.title}
        </h2>
        <p className="mt-2 text-sm text-gray-700">{refusal.message}</p>
        <p className="mt-1 text-sm text-gray-700">{refusal.action}</p>
        {wrong ? (
          <div className="mt-3">
            <CatalogErrorBox error={AUTH_008} />
          </div>
        ) : null}
        <form onSubmit={submit} className="mt-4 flex items-end gap-2">
          <input
            data-testid="step-up-code"
            aria-label="Code from your authenticator app"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={7}
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="w-32 rounded border border-gray-300 px-2 py-1 font-mono tracking-widest"
          />
          <button
            type="submit"
            disabled={busy || code.trim().length < 6}
            className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            Confirm
          </button>
          <button type="button" onClick={() => finish(false)} className="px-2 py-1.5 text-sm text-gray-600">
            Cancel
          </button>
        </form>
      </div>
    </div>
  );
}
