"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { apiFetch } from "@/lib/api";

type Identity = {
  email: string;
  tenant_id: string | null;
  role: string | null;
  is_platform_admin: boolean;
};

export default function Home() {
  const [identity, setIdentity] = useState<Identity | "signed_out" | "loading">("loading");

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) {
        setIdentity("signed_out");
        return;
      }
      apiFetch("/auth/me")
        .then((res) => (res.ok ? res.json() : Promise.reject()))
        .then(setIdentity)
        .catch(() => setIdentity("signed_out"));
    });
  }, []);

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
          <div className="space-y-2 text-left">
            <p>Signed in as {identity.email}</p>
            {identity.is_platform_admin && (
              <p>
                <Link href="/admin/tenants/new" className="underline">
                  Founder Console → Create tenant
                </Link>
              </p>
            )}
            {identity.tenant_id && <p className="text-sm text-gray-500">Role: {identity.role}</p>}
          </div>
        )}
      </div>
    </main>
  );
}
