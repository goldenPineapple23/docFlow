"use client";

import { useEffect, useState } from "react";
import { notFound, usePathname } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { AuthenticatorSetup } from "@/components/admin/AuthenticatorSetup";
import { ConsoleMfaBanner } from "@/components/admin/ConsoleMfaBanner";
import { ConsoleNav } from "@/components/admin/ConsoleNav";
import { StepUpDialog } from "@/components/admin/StepUpDialog";
import { consoleTenantFromPath } from "@/lib/reviewScope";

type ConsoleMfa = { enforced: boolean; aal: string | null } | null;

/**
 * Gate for every /admin/* page. The real security boundary is the backend
 * (require_platform_admin in apps/api/app/deps.py, which 404s the API
 * itself) -- this is a client-side belt-and-suspenders check so a non-admin
 * doesn't even see the Console's UI chrome while a request to the backend
 * is in flight. CLAUDE.md Section 7.15.1 requires /admin/* to be
 * unreachable, not just hidden, for the API; a fuller server-rendered
 * check (avoiding the brief blank-render-then-404 this causes) is Phase 5
 * work once the Console's real layout exists -- see DECISIONS.md.
 *
 * MFA (D-151, D-177): with enforcement on, a session that hasn't passed an
 * authenticator challenge sees only the enrol/confirm screen -- the API
 * refuses every Console call with AUTH-006 anyway. With it off, every page
 * carries a banner saying so. The step-up dialog is mounted here for the
 * destructive actions.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<"checking" | "allowed" | "denied">("checking");
  const [mfa, setMfa] = useState<ConsoleMfa>(null);
  const pathname = usePathname();

  useEffect(() => {
    let cancelled = false;

    apiFetch("/auth/me")
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          setStatus("denied");
          return;
        }
        const data = await res.json();
        if (!data.is_platform_admin) {
          setStatus("denied");
          return;
        }
        setMfa(data.console_mfa ?? null);
        setStatus("allowed");
      })
      .catch(() => {
        if (!cancelled) setStatus("denied");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const reviewing = consoleTenantFromPath(pathname) !== null;

  if (status === "checking") {
    return null;
  }
  // notFound() only works while rendering, not inside the fetch callback
  // above -- calling it there left a non-admin on a blank page.
  if (status === "denied") {
    notFound();
  }

  if (mfa?.enforced && mfa.aal !== "aal2") {
    return (
      <main className="mx-auto max-w-lg p-6" data-testid="console-mfa-gate">
        <h1 className="text-xl font-semibold">Confirm it&apos;s you</h1>
        <p className="mt-1 mb-4 text-sm text-gray-600">
          The Console needs a code from your authenticator app as well as your password.
        </p>
        {/* A fresh page load picks up the upgraded session everywhere. */}
        <AuthenticatorSetup onDone={() => window.location.reload()} />
      </main>
    );
  }

  return (
    <>
      {mfa && !mfa.enforced ? <ConsoleMfaBanner /> : null}
      <ConsoleNav />
      <StepUpDialog />
      {/* The review screen sets its own (much wider) width: the document and
          its fields side by side don't fit the Console's reading column. */}
      {reviewing ? children : <main className="mx-auto max-w-6xl p-6">{children}</main>}
    </>
  );
}
