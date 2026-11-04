import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PageViewer } from "./PageViewer";
import { ReviewApiError, originalDocumentUrl, type DocumentPage, type OriginalDocument } from "@/lib/review";

vi.mock("@/lib/review", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/review")>()),
  originalDocumentUrl: vi.fn(),
}));

const minted = vi.mocked(originalDocumentUrl);

const DOC_032 = {
  code: "DOC-032",
  title: "This page can't be shown",
  message: "DocFlow couldn't show this page here. What DocFlow read from the document isn't affected.",
  action: "Download the original to see this page.",
};

const SYS_002 = {
  code: "SYS-002",
  title: "Files can't be opened right now",
  message: "A setting is missing on our side.",
  action: "DocFlow has logged this. If it keeps happening, contact DocFlow support.",
};

function pages(token: string, count = 3): DocumentPage[] {
  return Array.from({ length: count }, (_, index) => ({
    index,
    drawn: true as const,
    width: 1700,
    height: 2200,
    url: `/review/documents/d1/pages/${index}?token=${token}`,
  }));
}

function remint(token: string): OriginalDocument {
  return {
    view: "pages",
    pages: pages(token),
    page_unavailable: DOC_032,
    url: "/original/content?token=x",
    download_url: "/original/content?token=x&download=1",
    expires_at: 0,
    previewable: true,
    format: "a PDF",
    filename: "po.pdf",
    preview_kind: null,
  };
}

function viewer(initial: DocumentPage[] = pages("first")) {
  return render(
    <PageViewer documentId="d1" filename="po.pdf" initialPages={initial} pageUnavailable={DOC_032} />,
  );
}

const image = () => screen.getByTestId("page-image") as HTMLImageElement;

describe("the page viewer (D-233)", () => {
  beforeEach(() => minted.mockReset());

  it("shows one page at a time, with its place as the image's alt text", async () => {
    viewer();

    expect(screen.getByTestId("page-position")).toHaveTextContent("Page 1 of 3");
    expect(image()).toHaveAttribute("alt", "Page 1 of 3");
    expect(screen.getByTestId("page-prev")).toBeDisabled();

    await userEvent.click(screen.getByTestId("page-next"));
    await userEvent.click(screen.getByTestId("page-next"));

    expect(image()).toHaveAttribute("alt", "Page 3 of 3");
    expect(image().src).toMatch(/pages\/2\?token=first$/);
    expect(screen.getByTestId("page-next")).toBeDisabled();
  });

  it("zooms with buttons, not only gestures, and fits to width again", async () => {
    viewer();

    expect(image().style.width).toBe("100%");
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(image().style.width).toBe("125%");
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(image().style.width).toBe("80%");
    await userEvent.click(screen.getByRole("button", { name: "Fit to width" }));
    expect(image().style.width).toBe("100%");
  });

  it("pinches to zoom with two pointers", () => {
    viewer();
    const scroller = screen.getByTestId("page-scroller");

    fireEvent.pointerDown(scroller, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerDown(scroller, { pointerId: 2, clientX: 200, clientY: 100 });
    fireEvent.pointerMove(scroller, { pointerId: 2, clientX: 300, clientY: 100 });

    expect(image().style.width).toBe("200%");
  });

  it("shows DOC-032 straight away for a page that wasn't drawn", async () => {
    viewer([...pages("first", 1), { index: 1, drawn: false }]);

    await userEvent.click(screen.getByTestId("page-next"));

    const panel = screen.getByTestId("page-unavailable");
    expect(panel).toHaveTextContent("This page can't be shown");
    expect(panel).toHaveTextContent("DOC-032");
    expect(screen.getByTestId("download-original")).toBeInTheDocument();
    expect(minted).not.toHaveBeenCalled();
  });

  it("mints once more and retries once, then says DOC-032: never a loop", async () => {
    minted.mockResolvedValue(remint("second"));
    viewer();

    fireEvent.error(image());
    await waitFor(() => expect(image().src).toMatch(/token=second$/));
    fireEvent.error(image());

    expect(await screen.findByTestId("page-unavailable")).toHaveTextContent("DOC-032");
    expect(minted).toHaveBeenCalledTimes(1);
  });

  it("gives a page that failed one more try when the reviewer leaves and returns to it", async () => {
    minted.mockResolvedValueOnce(remint("second")).mockResolvedValueOnce(remint("third"));
    viewer();

    // The first visit spends its budget: one re-mint, one retry, DOC-032.
    fireEvent.error(image());
    await waitFor(() => expect(image().src).toMatch(/pages\/0\?token=second$/));
    fireEvent.error(image());
    expect(await screen.findByTestId("page-unavailable")).toHaveTextContent("DOC-032");
    expect(minted).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByTestId("page-next"));
    await userEvent.click(screen.getByTestId("page-prev"));

    // A new visit: the page shows again, and has exactly one more re-mint.
    expect(image().src).toMatch(/pages\/0\?token=second$/);
    fireEvent.error(image());
    await waitFor(() => expect(image().src).toMatch(/pages\/0\?token=third$/));
    expect(minted).toHaveBeenCalledTimes(2);
    fireEvent.error(image());
    expect(await screen.findByTestId("page-unavailable")).toHaveTextContent("DOC-032");
    expect(minted).toHaveBeenCalledTimes(2);
  });

  it.each(["resolves", "rejects"] as const)(
    "a re-mint still running when the reviewer leaves can't spend the next visit's budget (it %s late)",
    async (outcome) => {
      let settle: () => void = () => {};
      const held = new Promise<OriginalDocument>((resolve, reject) => {
        settle = () =>
          outcome === "resolves" ? resolve(remint("second")) : reject(new ReviewApiError(SYS_002, 503));
      });
      minted.mockReturnValueOnce(held).mockResolvedValueOnce(remint("third"));
      viewer();

      // The image fails and its re-mint is held; the reviewer leaves and returns.
      fireEvent.error(image());
      expect(minted).toHaveBeenCalledTimes(1);
      await userEvent.click(screen.getByTestId("page-next"));
      await userEvent.click(screen.getByTestId("page-prev"));
      await act(async () => {
        settle();
        await held.catch(() => undefined);
      });

      // The new visit has its own full budget: one re-mint and one retry.
      expect(screen.queryByTestId("page-unavailable")).toBeNull();
      fireEvent.error(image());
      await waitFor(() => expect(image().src).toMatch(/pages\/0\?token=third$/));
      expect(minted).toHaveBeenCalledTimes(2);
      fireEvent.error(image());
      expect(await screen.findByTestId("page-unavailable")).toHaveTextContent("DOC-032");
      expect(minted).toHaveBeenCalledTimes(2);
    },
  );

  it("within one visit, still one re-mint and one retry, and then nothing more", async () => {
    minted.mockResolvedValue(remint("second"));
    viewer();

    fireEvent.error(image());
    await waitFor(() => expect(image().src).toMatch(/token=second$/));
    fireEvent.error(image());
    expect(await screen.findByTestId("page-unavailable")).toHaveTextContent("DOC-032");

    // Time passes on the same visit: no further mint, no image, no loop.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(minted).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("page-image")).toBeNull();
    expect(screen.getByTestId("page-unavailable")).toBeInTheDocument();
  });

  it("says DOC-032 when the fresh links can't be had", async () => {
    // Once: Vitest 5 fails a test whose standing mock rejects, even when the
    // rejection is caught (checked on a bare awaited call).
    minted.mockRejectedValueOnce(new ReviewApiError(SYS_002, 503));
    viewer();

    fireEvent.error(image());

    expect(await screen.findByTestId("page-unavailable")).toHaveTextContent("DOC-032");
    expect(minted).toHaveBeenCalledTimes(1);
  });

  it("uses the new token for later pages once a re-mint succeeds", async () => {
    minted.mockResolvedValue(remint("second"));
    viewer();

    fireEvent.error(image());
    await waitFor(() => expect(image().src).toMatch(/pages\/0\?token=second$/));
    await userEvent.click(screen.getByTestId("page-next"));

    expect(image().src).toMatch(/pages\/1\?token=second$/);
    expect(minted).toHaveBeenCalledTimes(1);
  });
});
