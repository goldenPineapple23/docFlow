"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

/**
 * The Console's own bar (CLAUDE.md Section 7.15). Visibly different from the
 * tenant surface's header -- dark, and labelled "Console" -- so the founder
 * always knows which side of the wall they are on.
 */
const LINKS = [
  { href: "/admin", label: "Attention" },
  { href: "/admin/intakes", label: "Intakes" },
  { href: "/admin/tenants", label: "Tenants" },
  { href: "/admin/lifecycle", label: "Lifecycle" },
  { href: "/admin/outbox", label: "Outbox" },
];

export function ConsoleNav() {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <header className="sticky top-0 z-20 bg-slate-900 text-slate-100">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-x-4 gap-y-2 px-5 py-3">
        {/* Wraps on a narrow screen rather than pushing Sign out off the page. */}
        <nav className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-1 text-sm">
          <span className="flex items-center gap-2 font-semibold tracking-tight">
            {/* The light version of the logo, made for this dark bar. */}
            <Image src="/docflow-logo-light.png" alt="DocFlow" width={402} height={86} priority className="h-5 w-auto" />
            <span className="font-medium text-[#6EBEE1]">Console</span>
          </span>
          {LINKS.map((link) => {
            const active =
              link.href === "/admin" ? pathname === "/admin" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={active ? "font-medium text-white" : "text-slate-400 hover:text-white"}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
        <button
          type="button"
          onClick={async () => {
            await supabase.auth.signOut();
            router.push("/login");
          }}
          className="shrink-0 rounded-md border border-slate-600 px-2.5 py-1 text-sm text-slate-200 hover:bg-slate-800"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
