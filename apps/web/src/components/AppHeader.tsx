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
    <header className="border-b border-gray-200">
      <div className="mx-auto flex max-w-[110rem] items-center justify-between gap-4 px-4 py-2">
        <nav className="flex items-center gap-4">
          <Link href="/review" className="font-semibold">
            DocFlow
          </Link>
          <Link href="/review" className="text-sm text-blue-700 underline">
            Orders to review
          </Link>
        </nav>

        <div className="flex items-center gap-3 text-sm">
          {email ? <span className="text-gray-600">{email}</span> : null}
          <button
            type="button"
            onClick={() => void signOut()}
            data-testid="sign-out"
            className="rounded border border-gray-300 px-2 py-1"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
