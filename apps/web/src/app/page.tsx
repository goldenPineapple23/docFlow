"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { apiFetch } from "@/lib/api";

type Identity = {
  email: string;
  tenant_id: string | null;
  role: string | null;
  is_platform_admin: boolean;
};

/**
 * Where signing in lands you.
 *
 * **A tenant user is sent straight to their queue.** This page used to show
 * a signed-in user their own email address and nothing else -- no link to
 * the review screen, which is the entire product for them. Somebody doing
 * the Phase 3 walkthrough signed in, read "Role: reviewer", and had nowhere
 * to go (DECISIONS.md D-090). Signing in should put you in front of your
 * work, not in front of a fact about yourself.
 *
 * The founder's own account has no tenant, so it stays here and gets the
 * Console links instead.
 */
export default function Home() {
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity | "signed_out" | "loading">("loading");

  useEffect(() => {
    let cancelled = false;
    supabase.auth
      .getSession()
      .then(({ data: { session } }) => {
        if (!session) return "signed_out" as const;
        return apiFetch("/auth/me").then((res) => (res.ok ? res.json() : "signed_out" as const));
      })
      .then((next) => {
        if (cancelled) return;
        setIdentity(next);
        if (next !== "signed_out" && next.tenant_id) {
          router.replace("/review");
        }
      })
      .catch(() => {
        if (!cancelled) setIdentity("signed_out");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <div className="max-w-md space-y-4 text-center">
        <h1 className="text-2xl font-semibold">DocFlow</h1>

        {identity === "loading" && <p className="text-gray-500">Loading…</p>}

        {identity === "signed_out" && (
          <p>
            <Link href="/login" className="underline">
              Sign in
            </Link>{" "}
            to continue.
          </p>
        )}

        {identity !== "loading" && identity !== "signed_out" && (
          <div className="space-y-3">
            <p>Signed in as {identity.email}</p>

            {identity.tenant_id ? (
              <p>
                <Link href="/review" className="underline">
                  Go to orders to review
                </Link>
              </p>
            ) : null}

            {identity.is_platform_admin && (
              <div className="space-y-1 text-sm">
                <p>
                  <Link href="/admin/tenants/new" className="underline">
                    Founder Console → Create tenant
                  </Link>
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
