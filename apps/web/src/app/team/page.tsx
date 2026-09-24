"use client";

import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";
import { ReviewApiError, UNEXPECTED, type CatalogError } from "@/lib/review";
import {
  getTeam,
  inviteReviewer,
  removeMember,
  resendInvite,
  roleLabel,
  sentNotice,
  type Member,
} from "@/lib/team";

/**
 * The people on the account (Section 3: "every subsequent user is invited by a
 * tenant owner/admin"). Slice 5.8d, D-132 -- the founder's option A: list,
 * invite a reviewer, resend, remove. No role changes and no second admin.
 *
 * A reviewer who types the address gets the API's AUTH-003, rendered as given.
 * Removing asks once more, inline, because it takes effect on the person's very
 * next click.
 */

function asCatalog(e: unknown): CatalogError {
  return e instanceof ReviewApiError ? e.catalog : UNEXPECTED;
}

function Status({ member }: { member: Member }) {
  if (member.signed_in) return <span className="text-gray-600">Signed in</span>;
  if (member.invite_sent_at)
    return (
      <span className="text-amber-700" title={new Date(member.invite_sent_at).toLocaleString()}>
        Invite sent {new Date(member.invite_sent_at).toLocaleDateString()}
      </span>
    );
  return <span className="text-gray-500">Not invited yet</span>;
}

export default function TeamPage() {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [loadError, setLoadError] = useState<CatalogError | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<CatalogError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const reload = useCallback(
    () =>
      getTeam()
        .then((data) => {
          setMembers(data.members);
          setLoadError(null);
        })
        .catch((e) => setLoadError(asCatalog(e))),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    getTeam()
      .then((data) => {
        if (!cancelled) setMembers(data.members);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(asCatalog(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function run(action: () => Promise<string>) {
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      setNotice(await action());
      await reload();
    } catch (e) {
      setActionError(asCatalog(e));
    } finally {
      setBusy(false);
    }
  }

  function onInvite(e: React.FormEvent) {
    e.preventDefault();
    const address = email.trim();
    if (!address) return;
    void run(async () => {
      const result = await inviteReviewer(address);
      setEmail("");
      return sentNotice(result, result.restored ? "Welcomed back and re-invited" : "Invited");
    });
  }

  function onRemove(m: Member) {
    setConfirming(null);
    void run(async () => {
      const r = await removeMember(m.user_id);
      return `Removed ${r.email}. They can no longer sign in; their past work stays in Activity.`;
    });
  }

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-4xl p-6">
        <h1 className="text-xl font-semibold">Team</h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-600">
          The people who can sign in to this account. Reviewers can review, approve, export and
          upload purchase orders; only you can add or remove people.
        </p>

        {loadError ? (
          <div className="mt-4 max-w-2xl">
            <CatalogErrorBox error={loadError} />
          </div>
        ) : null}

        {!loadError && members === null ? (
          <p className="mt-4 text-sm text-gray-500">Loading…</p>
        ) : null}

        {members ? (
          <>
            <form
              onSubmit={onInvite}
              data-testid="invite-form"
              className="mt-5 flex max-w-2xl flex-wrap items-end gap-2 rounded-xl border border-gray-200 bg-white p-4"
            >
              <label className="flex min-w-[16rem] flex-1 flex-col gap-1 text-sm">
                <span className="font-medium">Invite a reviewer</span>
                <input
                  type="email"
                  required
                  autoComplete="off"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@company.com"
                  data-testid="invite-email"
                  className="rounded-md border border-gray-300 px-3 py-2"
                />
              </label>
              <button
                type="submit"
                disabled={busy}
                data-testid="invite-submit"
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                Send invite
              </button>
            </form>

            {notice ? (
              <p
                role="status"
                data-testid="team-notice"
                className="mt-3 max-w-2xl rounded border border-blue-200 bg-blue-50 p-3 text-sm"
              >
                {notice}
              </p>
            ) : null}
            {actionError ? (
              <div className="mt-3 max-w-2xl">
                <CatalogErrorBox error={actionError} />
              </div>
            ) : null}

            <ul
              data-testid="team-list"
              className="mt-5 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm"
            >
              {members.map((m) => (
                <li
                  key={m.user_id}
                  data-testid="team-member"
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3"
                >
                  <span className="min-w-0 flex-1 break-all">
                    <span className="font-medium">{m.full_name ?? m.email}</span>
                    {m.full_name ? <span className="ml-2 text-gray-500">{m.email}</span> : null}
                    {m.is_you ? <span className="ml-2 text-gray-500">(you)</span> : null}
                  </span>
                  <span className="w-20 text-gray-700">{roleLabel(m.role)}</span>
                  <span className="w-40">
                    <Status member={m} />
                  </span>
                  <span className="flex w-48 justify-end gap-2">
                    {confirming === m.user_id ? (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          data-testid="remove-confirm"
                          onClick={() => onRemove(m)}
                          className="rounded-md bg-red-700 px-2.5 py-1 font-medium text-white disabled:opacity-50"
                        >
                          Remove
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirming(null)}
                          className="rounded-md border border-gray-300 bg-white px-2.5 py-1 font-medium text-gray-700"
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        {m.can_resend ? (
                          <button
                            type="button"
                            disabled={busy}
                            data-testid="resend"
                            onClick={() =>
                              void run(async () =>
                                sentNotice(await resendInvite(m.user_id), "Sent a new invite to"),
                              )
                            }
                            className="rounded-md border border-gray-300 bg-white px-2.5 py-1 font-medium text-gray-700 disabled:opacity-50"
                          >
                            Resend invite
                          </button>
                        ) : null}
                        {m.can_remove ? (
                          <button
                            type="button"
                            disabled={busy}
                            data-testid="remove"
                            onClick={() => setConfirming(m.user_id)}
                            className="rounded-md border border-red-300 bg-white px-2.5 py-1 font-medium text-red-700 disabled:opacity-50"
                          >
                            Remove…
                          </button>
                        ) : null}
                      </>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </main>
    </>
  );
}
