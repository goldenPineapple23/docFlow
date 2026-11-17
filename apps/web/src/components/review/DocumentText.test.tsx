import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentText } from "./DocumentText";
import { documentText } from "@/lib/review";

vi.mock("@/lib/review", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/review")>()),
  documentText: vi.fn(),
}));

const stored = vi.mocked(documentText);

// What the worker stores for an HTML file whose sender wrote markup as
// entities (B1 slice 2): the parse service's plain text, markup characters
// and all. CLAUDE.md 7.12: shown escaped, never as HTML.
const HTML_FILE_TEXT =
  '4 x TEST-100\n<script>window.__ran = true</script><img src="x" onerror="window.__ran = true"><b>bold</b>';

describe("DocumentText", () => {
  beforeEach(() => {
    stored.mockReset();
    delete (window as { __ran?: boolean }).__ran;
  });

  it("shows an HTML file's stored text as the characters it is, and makes no element from it", async () => {
    stored.mockResolvedValueOnce({ kind: "extracted_text", text: HTML_FILE_TEXT, truncated: false, cut_at: 200000 });

    render(<DocumentText documentId="doc-1" format="a web page" filename="order.html" />);

    const shown = await screen.findByTestId("document-text");
    expect(shown.textContent).toBe(HTML_FILE_TEXT);
    expect(shown.querySelectorAll("script, img, b")).toHaveLength(0);
    expect(shown.children).toHaveLength(0);
    expect(document.querySelectorAll("script[src], img[onerror]")).toHaveLength(0);
    expect((window as { __ran?: boolean }).__ran).toBeUndefined();
    expect(screen.getByTestId("preview-kind").textContent).toContain("Text DocFlow read from a web page");
  });
});
