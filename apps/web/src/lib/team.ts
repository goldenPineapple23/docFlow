import { request } from "./review";

/**
 * The Team page (slice 5.8d, D-132): the account's admin lists the people on
 * the account, invites a reviewer, resends an invite and removes someone.
 *
 * Every rule is enforced by the API; `can_remove` and `can_resend` only decide
 * which buttons are worth showing. A refusal arrives as a catalog entry and is
 * rendered as given (7.16.5).
 */

export type Member = {
  user_id: string;
  email: string;
  full_name: string | null;
  role: "owner" | "admin" | "reviewer" | "viewer";
  invite_sent_at: string | null;
  signed_in: boolean;
  is_you: boolean;
  can_remove: boolean;
  can_resend: boolean;
};

export type InviteResult = { user_id: string; email: string; held: boolean; restored?: boolean };

export const getTeam = () => request<{ members: Member[] }>("/team");

export const inviteReviewer = (email: string) =>
  request<InviteResult>("/team/invite", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

export const resendInvite = (userId: string) =>
  request<InviteResult>(`/team/${encodeURIComponent(userId)}/resend`, { method: "POST" });

export const removeMember = (userId: string) =>
  request<{ user_id: string; email: string }>(`/team/${encodeURIComponent(userId)}/remove`, {
    method: "POST",
  });

/** The role as the customer knows it: the first user is stored as `owner` and
 * shown as "Admin" (D-128). */
export function roleLabel(role: Member["role"]): string {
  if (role === "owner" || role === "admin") return "Admin";
  if (role === "viewer") return "Viewer";
  return "Reviewer";
}

/** What the page says after an invite goes out. With no email provider
 * configured (every development machine, and staging until a sending domain
 * exists) the invite waits in DocFlow's outbox, and the page says so rather than
 * claiming it was emailed. */
export function sentNotice(result: InviteResult, verb: string): string {
  const base = `${verb} ${result.email}.`;
  return result.held
    ? `${base} Email sending isn't switched on for DocFlow yet, so DocFlow is holding the invite and will pass it on.`
    : `${base} They'll get an email with a link to choose a password.`;
}
