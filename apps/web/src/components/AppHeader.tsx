"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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
export function AppHeader({ email }: { email?: string | null }) {
  const router = useRouter();

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
              className="grid h-7 w-7 place-items-center rounded-md bg-slate-900 text-xs font-bold text-white"
            >
              DF
            </span>
            <span className="text-[15px] font-semibold tracking-tight text-slate-900">DocFlow</span>
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
