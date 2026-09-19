"use client";

import { useEffect, useState } from "react";
import { notFound, usePathname } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { ConsoleNav } from "@/components/admin/ConsoleNav";
import { consoleTenantFromPath } from "@/lib/reviewScope";

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
  const pathname = usePathname();

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

  const reviewing = consoleTenantFromPath(pathname) !== null;

  if (status === "checking") {
    return null;
  }

  return (
    <>
      <ConsoleNav />
      {/* The review screen sets its own (much wider) width: the document and
          its fields side by side don't fit the Console's reading column. */}
      {reviewing ? children : <main className="mx-auto max-w-6xl p-6">{children}</main>}
    </>
  );
}
