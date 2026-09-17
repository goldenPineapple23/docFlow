import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Rules about the review surface that are easier to enforce by reading the
 * source than by rendering it (CLAUDE.md Section 10).
 *
 * These exist because the failure they prevent is silent: a single
 * `dangerouslySetInnerHTML` added in a hurry would render a buyer's document
 * text as markup, and nothing on screen would look wrong until someone sent
 * a document that exploited it.
 *
 * The patterns below match USAGE, not mentions -- a JSX prop or an object
 * key, both of which are followed by `=` or `:`. Prose in a comment saying
 * "there is no dangerouslySetInnerHTML here" is not a violation, and an
 * earlier version of this test that matched the bare word failed on exactly
 * that.
 */

const SRC = path.resolve(__dirname, "..");
const USAGE = /dangerouslySetInnerHTML\s*[=:]/;

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      // Test files are excluded: they quote the patterns in order to assert
      // against them.
      found.push(full);
    }
  }
  return found;
}

describe("the review surface never renders document text as HTML", () => {
  it("contains no dangerouslySetInnerHTML anywhere", () => {
    const offenders = sourceFiles(SRC).filter((file) => USAGE.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });

  it("renders the original document only inside a sandboxed iframe", () => {
    const iframes = sourceFiles(SRC).filter((file) =>
      readFileSync(file, "utf8").includes("<iframe"),
    );
    // One iframe in the whole app: the document viewer.
    expect(iframes).toHaveLength(1);

    const source = readFileSync(iframes[0], "utf8");
    // Assert the ATTRIBUTE's value, not the presence of a word: the file's
    // own comment lists the tokens that are deliberately absent, and an
    // earlier version of this test failed on that comment rather than on
    // anything real.
    const sandbox = source.match(/sandbox="([^"]*)"/);
    expect(sandbox).not.toBeNull();
    // The empty string is the most restrictive value there is -- no scripts,
    // no same-origin, no forms, no popups (Section 7.12).
    expect(sandbox?.[1]).toBe("");
  });
});
