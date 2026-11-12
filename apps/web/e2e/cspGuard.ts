import { type BrowserContext, type TestInfo } from "@playwright/test";

/**
 * Fails a test whose page broke the web app's Content-Security-Policy
 * (D-237). The policy blocks a request before it leaves the browser, so a
 * blocked page image, frame or API call would otherwise show only as
 * something not loading -- and `page.route` stubs never see it, because the
 * browser refuses the request before routing.
 *
 * Two witnesses, so neither browser's way of reporting is relied on alone:
 * the `securitypolicyviolation` event, logged by an init script, and the
 * browser's own console message. Only violations of the web app's own pages
 * count: the original's frame is served by the API under the API's policy,
 * which is the API's to enforce.
 */

const MARK = "[csp-violation]";

function originOf(url: string | undefined): string {
  try {
    return url ? new URL(url).origin : "";
  } catch {
    return "";
  }
}

export async function watchCsp(context: BrowserContext, testInfo: TestInfo): Promise<string[]> {
  const appOrigin = originOf(testInfo.project.use.baseURL);
  const seen: string[] = [];

  await context.addInitScript((mark: string) => {
    document.addEventListener("securitypolicyviolation", (event) => {
      console.error(`${mark} ${location.origin} ${event.effectiveDirective} ${event.blockedURI}`);
    });
  }, MARK);

  context.on("console", (message) => {
    const text = message.text();
    if (text.startsWith(MARK)) {
      if (text.split(" ")[1] === appOrigin) seen.push(text);
      return;
    }
    if (!/Content[- ]Security[- ]Policy/i.test(text)) return;
    const from = originOf(message.location().url) || originOf(message.page()?.url());
    if (from === appOrigin) seen.push(text);
  });

  return seen;
}
