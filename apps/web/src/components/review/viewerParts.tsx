"use client";

import { useState } from "react";
import { API_BASE_URL } from "@/lib/origins";
import {
  ReviewApiError,
  UNEXPECTED,
  originalDocumentUrl,
  triggerDownload,
  type CatalogError,
} from "@/lib/review";

/**
 * What the viewer's panels share (D-233): where the API is, the catalog's
 * words for a failure, the download, and the panel itself.
 */

// The same value the CSP allows for images and frames (src/lib/origins.ts).
export { API_BASE_URL };

export function catalogEntryOf(error: unknown): CatalogError {
  return error instanceof ReviewApiError ? error.catalog : UNEXPECTED;
}

/**
 * Mints its link on the click, not with the panel: a link lasts a few minutes
 * and the panel can sit open for longer, and an expired one is a bare 404.
 */
export function DownloadOriginal({ documentId, filename }: { documentId: string; filename: string }) {
  const [working, setWorking] = useState(false);
  const [failure, setFailure] = useState<CatalogError | null>(null);

  async function download() {
    setWorking(true);
    setFailure(null);
    try {
      const fresh = await originalDocumentUrl(documentId);
      triggerDownload(`${API_BASE_URL}${fresh.download_url}`);
    } catch (error) {
      setFailure(catalogEntryOf(error));
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={download}
        disabled={working}
        data-testid="download-original"
        className="mt-3 inline-block rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-60 max-xl:min-h-11"
      >
        Download {filename}
      </button>
      {failure ? (
        <div data-testid="download-failed" className="mt-3">
          <CatalogMessage entry={failure} />
        </div>
      ) : null}
    </>
  );
}

export function CatalogMessage({ entry }: { entry: CatalogError }) {
  return (
    <>
      <p className="text-sm font-medium text-gray-900">{entry.title}</p>
      <p className="mt-1 text-sm text-gray-600">{entry.message}</p>
      <p className="mt-1 text-sm text-gray-600">{entry.action}</p>
      <p className="mt-2 text-xs text-gray-400">{entry.code}</p>
    </>
  );
}

export function Panel({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className="flex h-full items-center justify-center rounded-xl border border-gray-200 bg-gray-50/60 p-6 text-center"
    >
      <div className="max-w-sm">{children}</div>
    </div>
  );
}
