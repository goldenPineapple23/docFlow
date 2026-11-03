import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentViewer } from "./DocumentViewer";
import { ReviewApiError, originalDocumentUrl, triggerDownload } from "@/lib/review";

vi.mock("@/lib/review", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/review")>()),
  originalDocumentUrl: vi.fn(),
  triggerDownload: vi.fn(),
}));

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

  it("keeps a text or image in the one sandboxed frame", async () => {
    minted.mockResolvedValue({
      ...aPdf("first"),
      previewable: true,
      format: null,
      not_shown: null,
    });
    render(<DocumentViewer documentId="d1" filename="po.txt" />);

    expect(await screen.findByTestId("document-viewer")).toHaveAttribute("sandbox", "");
  });
});
