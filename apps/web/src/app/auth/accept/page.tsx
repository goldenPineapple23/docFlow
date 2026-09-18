"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { PasswordInput } from "@/components/PasswordInput";

/**
 * Where an invite link lands (CLAUDE.md Section 7.15.2 Step 3; D-105).
 *
 * The link is a one-time Supabase link DocFlow generated and emailed. Opening
 * it signs the invited person in (the browser client reads the session from
 * the URL); this page then asks them to choose a password, which is how they
 * will sign in from now on. It is not a signup page: without a valid invite
 * link there is no session, and the page says so and offers nothing else
 * (Section 3: no public signup).
 */

const MIN_PASSWORD_LENGTH = 10;

export default function AcceptInvitePage() {
  const router = useRouter();
  const [state, setState] = useState<"checking" | "ready" | "no-session">("checking");
  const [email, setEmail] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // The client finishes reading the link's session asynchronously; the
    // auth-state event is the reliable signal, getSession the fallback.
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      if (cancelled || !session) return;
      setEmail(session.user.email ?? null);
      setState("ready");
    });
    const timer = setTimeout(() => {
      supabase.auth.getSession().then(({ data }) => {
        if (cancelled) return;
        if (data.session) {
          setEmail(data.session.user.email ?? null);
          setState("ready");
        } else {
          setState((current) => (current === "checking" ? "no-session" : current));
        }
      });
    }, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      subscription.subscription.unsubscribe();
    };
  }, []);

  if (state === "checking") {
    return <main className="p-8 text-sm text-gray-500">Checking your invite link…</main>;
  }

  if (state === "no-session") {
    return (
      <main className="mx-auto max-w-sm p-8">
        <h1 className="text-xl font-semibold">This invite link has expired</h1>
        <p className="mt-2 text-sm text-gray-600">
          Invite links work once and only for a limited time, so this one can&apos;t sign you in.
        </p>
        <p className="mt-2 text-sm text-gray-600">
          Reply to the invite email and ask for a new link. Nothing about your account has changed.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-sm p-8">
      <h1 className="text-xl font-semibold">Choose your password</h1>
      <p className="mt-1 text-sm text-gray-600">
        {email ? `You're setting the password for ${email}.` : "Set the password you'll sign in with."}{" "}
        Use at least {MIN_PASSWORD_LENGTH} characters.
      </p>
      <form
        className="mt-5 space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          setProblem(null);
          if (password.length < MIN_PASSWORD_LENGTH) {
            setProblem(`That password is shorter than ${MIN_PASSWORD_LENGTH} characters. Choose a longer one.`);
            return;
          }
          if (password !== confirm) {
            setProblem("The two passwords don't match, so nothing was saved. Type them again.");
            return;
          }
          setSaving(true);
          const { error } = await supabase.auth.updateUser({ password });
          setSaving(false);
          if (error) {
            setProblem(
              "We couldn't save that password — your invite may have expired while this page was open. Reply to the invite email for a new link.",
            );
            return;
          }
          router.push("/review");
        }}
      >
        <label className="block text-sm">
          <span className="font-medium">New password</span>
          <PasswordInput
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            data-testid="accept-password"
          />
        </label>
        <label className="block text-sm">
          <span className="font-medium">Type it again</span>
          <PasswordInput
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            data-testid="accept-confirm"
          />
        </label>
        {problem ? <p role="alert" className="text-sm text-red-700">{problem}</p> : null}
        <button
          type="submit"
          disabled={saving}
          data-testid="accept-submit"
          className="w-full rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save password and continue"}
        </button>
      </form>
    </main>
  );
}
