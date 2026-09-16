"use client";

import { useEffect, useState } from "react";
import { notFound } from "next/navigation";
import { apiFetch } from "@/lib/api";

/**
 * Gate for every /admin/* page. The real security boundary is the backend
 * (require_platform_admin in apps/api/app/deps.py, which 404s the API
 * itself) -- this is a client-side belt-and-suspenders check so a non-admin
 * doesn't even see the Console's UI chrome while a request to the backend
 * is in flight. CLAUDE.md Section 7.15.1 requires /admin/* to be
 * unreachable, not just hidden, for the API; a fuller server-rendered
 * check (avoiding the brief blank-render-then-404 this causes) is Phase 5
 * work once the Console's real layout exists -- see DECISIONS.md.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<"checking" | "allowed">("checking");

  useEffect(() => {
    let cancelled = false;

    apiFetch("/auth/me")
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          notFound();
          return;
        }
        const data = await res.json();
        if (!data.is_platform_admin) {
          notFound();
          return;
        }
        setStatus("allowed");
      })
      .catch(() => {
        if (!cancelled) notFound();
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (status === "checking") {
    return null;
  }

  return <>{children}</>;
}
