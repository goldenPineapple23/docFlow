import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentViewer } from "./DocumentViewer";
import { ReviewApiError, documentText, originalDocumentUrl, triggerDownload } from "@/lib/review";

vi.mock("@/lib/review", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/review")>()),
  originalDocumentUrl: vi.fn(),
  documentText: vi.fn(),
  triggerDownload: vi.fn(),
}));

const DOC_032 = {
  code: "DOC-032",
  title: "This page can't be shown",
  message: "DocFlow couldn't show this page here. What DocFlow read from the document isn't affected.",
  action: "Download the original to see this page.",
};

const DOC_033 = {
  code: "DOC-033",
  title: "There's no text to show for this file",
  message: "DocFlow has no stored text from this file to show here.",
  action: "Download the original to read it.",
};

function aPage(index: number, token: string) {
  return { index, drawn: true as const, width: 1700, height: 2200, url: `/pages/${index}?token=${token}` };
}

const minted = vi.mocked(originalDocumentUrl);
const downloaded = vi.mocked(triggerDownload);

const SYS_002 = {
  code: "SYS-002",
  title: "Files can't be opened right now",
  message: "A setting is missing on our side.",
  action: "DocFlow has logged this. If it keeps happening, contact DocFlow support.",
};

function aPdf(token: string) {
  return {
    view: "panel" as const,
    url: `/original/content?token=${token}`,
    download_url: `/original/content?token=${token}&download=1`,
    expires_at: 0,
    previewable: false,
    format: "a PDF",
    filename: "po.pdf",
    preview_kind: null,
    unavailable: null,
    not_shown: {
      code: "DOC-031",
      title: "This PDF can't be shown here",
      message: "It can't show the pages in this panel.",
      action: "Download the original if you need to check a value against it.",
    },
  };
}

describe("the original-document viewer (Section 7.12, D-228)", () => {
  beforeEach(() => {
    minted.mockReset();
    downloaded.mockReset();
  });

  it("gives a PDF no frame, and says why in the catalog's words", async () => {
    minted.mockResolvedValue(aPdf("first"));
    render(<DocumentViewer documentId="d1" filename="po.pdf" />);

    expect(await screen.findByText("This PDF can't be shown here")).toBeInTheDocument();
    expect(screen.getByText("DOC-031")).toBeInTheDocument();
    expect(screen.queryByTestId("document-viewer")).not.toBeInTheDocument();
  });

  it("offers a button with no link to open in a tab", async () => {
    minted.mockResolvedValue(aPdf("first"));
    render(<DocumentViewer documentId="d1" filename="po.pdf" />);

    const button = await screen.findByTestId("download-original");
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveTextContent("Download po.pdf");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("mints a fresh link when the reviewer asks for the file", async () => {
    minted.mockResolvedValueOnce(aPdf("on-load")).mockResolvedValueOnce(aPdf("on-click"));
    render(<DocumentViewer documentId="d1" filename="po.pdf" />);

    await userEvent.click(await screen.findByTestId("download-original"));

    expect(minted).toHaveBeenCalledTimes(2);
    expect(downloaded).toHaveBeenCalledTimes(1);
    expect(downloaded.mock.calls[0][0]).toMatch(/token=on-click&download=1$/);
  });

  it("says so in the panel when the fresh link can't be had", async () => {
    minted
      .mockResolvedValueOnce(aPdf("on-load"))
      .mockRejectedValueOnce(new ReviewApiError(SYS_002, 503));
    render(<DocumentViewer documentId="d1" filename="po.pdf" />);

    await userEvent.click(await screen.findByTestId("download-original"));

    expect(await screen.findByTestId("download-failed")).toHaveTextContent("SYS-002");
    expect(downloaded).not.toHaveBeenCalled();
  });

  it("shows the catalog entry and its code when the first link fails", async () => {
    minted.mockRejectedValue(new ReviewApiError(SYS_002, 503));
    render(<DocumentViewer documentId="d1" filename="po.pdf" />);

    const panel = await screen.findByTestId("viewer-unavailable");
    expect(panel).toHaveTextContent("Files can't be opened right now");
    expect(panel).toHaveTextContent("SYS-002");
  });

  it("keeps a sender's image in the one sandboxed frame while its pages aren't drawn", async () => {
    minted.mockResolvedValue({
      ...aPdf("first"),
      view: "frame",
      previewable: true,
      format: null,
      not_shown: null,
    });
    render(<DocumentViewer documentId="d1" filename="po.png" />);

    expect(await screen.findByTestId("document-viewer")).toHaveAttribute("sandbox", "");
  });

  it("frames nothing when the API says pages or text (D-228 item 1's answer)", async () => {
    vi.mocked(documentText).mockResolvedValue({ kind: "extracted_text", text: "PO", truncated: false, cut_at: 200000 });
    for (const answer of [
      { ...aPdf("p"), view: "pages" as const, pages: [aPage(0, "p")], page_unavailable: DOC_032 },
      { ...aPdf("t"), view: "text" as const, not_shown: null },
    ]) {
      minted.mockResolvedValue(answer);
      const { unmount } = render(<DocumentViewer documentId="d1" filename="po" />);
      expect(await screen.findByTestId(answer.view === "pages" ? "page-viewer" : "document-text")).toBeInTheDocument();
      expect(screen.queryByTestId("document-viewer")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("says DOC-033 and offers the download when a text file has no stored text", async () => {
    minted.mockResolvedValue({ ...aPdf("first"), format: "a text file", not_shown: null, no_text: DOC_033 });
    render(<DocumentViewer documentId="d1" filename="po.txt" />);

    const panel = await screen.findByTestId("viewer-not-previewable");
    expect(panel).toHaveTextContent("There's no text to show for this file");
    expect(panel).toHaveTextContent("DOC-033");
    expect(screen.getByTestId("download-original")).toBeInTheDocument();
  });
});

describe("the stored text, in the page (D-233)", () => {
  beforeEach(() => {
    minted.mockReset();
    vi.mocked(documentText).mockReset();
  });

  it("shows markup in a document as the characters it is, never as HTML", async () => {
    const stored = '<img src="x" onerror="alert(1)"><script>alert(2)</script><b>bold</b>';
    minted.mockResolvedValue({ ...aPdf("first"), view: "text", format: "a text file", not_shown: null });
    vi.mocked(documentText).mockResolvedValue({ kind: "extracted_text", text: stored, truncated: false, cut_at: 200000 });
    render(<DocumentViewer documentId="d1" filename="po.txt" />);

    const shown = await screen.findByTestId("document-text");
    expect(shown.textContent).toBe(stored);
    expect(shown.querySelector("img, script, b")).toBeNull();
    expect(screen.getByTestId("preview-kind")).toHaveTextContent("Text DocFlow read from a text file");
    expect(screen.queryByTestId("text-cut")).not.toBeInTheDocument();
  });

  it("says where the text was cut, and offers the rest as the download", async () => {
    minted.mockResolvedValue({ ...aPdf("first"), view: "text", format: "a CSV file", not_shown: null });
    vi.mocked(documentText).mockResolvedValue({ kind: "extracted_text", text: "x", truncated: true, cut_at: 200000 });
    render(<DocumentViewer documentId="d1" filename="po.csv" />);

    const cut = await screen.findByTestId("text-cut");
    expect(cut).toHaveTextContent("Cut at 200,000 characters for viewing");
    expect(cut.querySelector("[data-testid=download-original]")).not.toBeNull();
  });

  it("shows the catalog's words, with the download, when the text can't be had", async () => {
    minted.mockResolvedValue({ ...aPdf("first"), view: "text", not_shown: null });
    vi.mocked(documentText).mockRejectedValueOnce(new ReviewApiError(DOC_033, 404));
    render(<DocumentViewer documentId="d1" filename="po.txt" />);

    const panel = await screen.findByTestId("viewer-text-unavailable");
    expect(panel).toHaveTextContent("DOC-033");
    expect(screen.getByTestId("download-original")).toBeInTheDocument();
  });
});
