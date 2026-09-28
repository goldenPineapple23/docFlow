import Link from "next/link";

/**
 * Shown on every Console page while CONSOLE_MFA_ENFORCED is off (DECISIONS.md
 * D-177: the founder's condition for shipping the setting off by default).
 * Not a failure, so not a catalog entry: it is a standing notice with a way
 * to fix it.
 */
export function ConsoleMfaBanner() {
  return (
    <div
      role="status"
      data-testid="console-mfa-banner"
      className="border-b border-amber-300 bg-amber-50 px-6 py-2 text-sm text-amber-900"
    >
      <strong>Authenticator codes aren&apos;t required yet.</strong> The Console is open to a
      password alone until MFA enforcement is switched on (CONSOLE_MFA_ENFORCED).{" "}
      <Link href="/admin/security" className="underline">
        Set up your authenticator
      </Link>
      , then switch it on — RUNBOOK section 4.
    </div>
  );
}
