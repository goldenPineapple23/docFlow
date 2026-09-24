/** Where to go after signing in. A page that found the person signed out sends
 * them here with `?next=` (D-134); only a path on this site is honoured, so a
 * crafted link cannot bounce someone to another site after they sign in. */
export function afterSignIn(search: string): string {
  const next = new URLSearchParams(search).get("next");
  if (next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")) {
    return next;
  }
  return "/";
}
