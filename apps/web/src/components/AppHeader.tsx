"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { supabase } from "@/lib/supabase";

/**
 * The bar across the top of every signed-in screen.
 *
 * It exists because the app had none: a reviewer signed in, landed on a page
 * with their name on it and no link to anything, and had no way to sign out
 * either. Every screen was reachable only by typing its URL, which is fine
 * for the person who built it and a dead end for everyone else
 * (DECISIONS.md D-090).
 */
export function AppHeader({
  email: emailProp,
  tenantName: tenantNameProp,
}: {
  email?: string | null;
  tenantName?: string | null;
}) {
  const router = useRouter();
  // The customer's own company name and email, from their session. Explicit
  // props win (tests, and any screen that already knows); otherwise the header
  // asks who is signed in. If that fails it just says "DocFlow" -- a header must
  // never stand in the way of the page.
  const [loaded, setLoaded] = useState<{ email: string | null; tenantName: string | null } | null>(null);
  const known = emailProp !== undefined || tenantNameProp !== undefined;
  useEffect(() => {
    if (known) return;
    let cancelled = false;
    apiFetch("/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((me) => {
        if (cancelled || !me) return;
        setLoaded({ email: me.email ?? null, tenantName: me.tenant_name ?? null });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [known]);
  const email = emailProp ?? loaded?.email ?? null;
  const tenantName = (tenantNameProp ?? loaded?.tenantName ?? "").trim() || null;

  useEffect(() => {
    // The browser tab says whose portal this is. (React escapes everything it
    // renders; document.title is plain text too.)
    document.title = tenantName ? `${tenantName} — DocFlow` : "DocFlow";
  }, [tenantName]);

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
  }

  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-[110rem] items-center justify-between gap-4 px-5 py-3">
        <nav className="flex items-center gap-5">
          <Link href="/review" className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="grid h-9 w-9 place-items-center rounded-md bg-slate-900 text-sm font-bold text-white"
            >
              DF
            </span>
            <span className="flex min-w-0 flex-col leading-tight">
              <span
                data-testid="tenant-name"
                title={tenantName ?? undefined}
                className="max-w-[14rem] truncate text-xl font-semibold tracking-tight text-slate-900 sm:max-w-[30rem] sm:text-2xl"
              >
                {tenantName ?? "DocFlow"}
              </span>
              {tenantName ? (
                <span className="text-xs font-medium tracking-wide text-slate-500">Powered by DocFlow</span>
              ) : null}
            </span>
          </Link>
          <Link
            href="/review"
            className="text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
          >
            Purchase orders
          </Link>
          <Link
            href="/held"
            className="text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
          >
            Held for review
          </Link>
        </nav>

        <div className="flex items-center gap-3 text-sm">
          {email ? <span className="hidden text-slate-500 sm:inline">{email}</span> : null}
          <button
            type="button"
            onClick={() => void signOut()}
            data-testid="sign-out"
            className="rounded-md border border-slate-300 bg-white px-2.5 py-1 font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
