"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { uploadOne, type UploadOutcome } from "@/lib/home";

/**
 * Uploading a purchase order (`docflow-mvp-features.docx`: "Upload PO (PDF,
 * Excel, image or scan)"). Slice 5.8a, D-128.
 *
 * The case that made this worth building: an order that arrives by post, or
 * handwritten over the counter. Someone photographs it and it goes in the same
 * queue as everything else.
 *
 * It calls the one upload endpoint email intake also uses, so the same
 * magic-byte checks, the same size and decompression limits, the same duplicate
 * detection and the same abuse ceilings apply (Section 10: no second upload
 * handler). Each file is its own request, so one refusal never costs the rest,
 * and every refusal is the catalog's own wording (7.16.5).
 */

const ACCEPT = [
  ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".csv", ".txt", ".rtf", ".md",
  ".html", ".htm", ".eml", ".msg", ".odt", ".ods",
  ".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".heic", ".heif",
].join(",");

export default function UploadPage() {
  const [queued, setQueued] = useState<File[]>([]);
  const [results, setResults] = useState<UploadOutcome[]>([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);

  function choose(list: FileList | null) {
    if (!list || list.length === 0) return;
    setResults([]);
    setQueued((prev) => [...prev, ...Array.from(list)]);
  }

  async function send() {
    setBusy(true);
    setDone(0);
    const outcomes: UploadOutcome[] = [];
    // One at a time: a person watching a phone upload photos over a poor
    // connection would rather see steady progress than several stalled bars.
    for (const file of queued) {
      outcomes.push(await uploadOne(file));
      setDone(outcomes.length);
      setResults([...outcomes]);
    }
    setQueued([]);
    if (fileInput.current) fileInput.current.value = "";
    if (cameraInput.current) cameraInput.current.value = "";
    setBusy(false);
  }

  const accepted = results.filter((r) => r.ok).length;

  return (
    <>
      <AppHeader />
      <main className="mx-auto max-w-3xl p-6">
        <h1 className="text-xl font-semibold">Upload a purchase order</h1>
        <p className="mt-1 text-sm text-gray-600">
          For orders that don&apos;t arrive by email — posted, handed over the counter, or
          handwritten. Take a photo or choose a file and it joins the same queue as everything
          else. PDF, Word, Excel, email files and photos all work.
        </p>

        <section className="mt-5 rounded-xl border border-dashed border-gray-300 bg-white p-6">
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={busy}
              data-testid="choose-files"
              onClick={() => fileInput.current?.click()}
              className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Choose files
            </button>
            <button
              type="button"
              disabled={busy}
              data-testid="take-photo"
              onClick={() => cameraInput.current?.click()}
              className="rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-50 sm:hidden"
            >
              Take a photo
            </button>
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={ACCEPT}
            aria-label="Choose purchase orders to upload"
            onChange={(e) => choose(e.target.files)}
            className="sr-only"
          />
          <input
            ref={cameraInput}
            type="file"
            accept="image/*"
            capture="environment"
            aria-label="Take a photo of a purchase order"
            onChange={(e) => choose(e.target.files)}
            className="sr-only"
          />

          <p className="mt-3 text-xs text-gray-500">
            One order per file. If an order runs to several pages, send it as a single PDF rather
            than one photo per page. Handwritten orders are read too, but expect to check every
            field before approving.
          </p>

          {queued.length > 0 ? (
            <div className="mt-4 border-t border-gray-100 pt-3">
              <ul data-testid="queued" className="space-y-1 text-sm">
                {queued.map((file, i) => (
                  <li key={`${file.name}-${i}`} className="flex items-center justify-between gap-3">
                    <span className="truncate">{file.name}</span>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={`Remove ${file.name}`}
                      onClick={() => setQueued((prev) => prev.filter((_, n) => n !== i))}
                      className="text-gray-400 hover:text-gray-700"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                disabled={busy}
                data-testid="send"
                onClick={() => void send()}
                className="mt-3 rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {busy
                  ? `Uploading ${done + 1} of ${queued.length}…`
                  : `Upload ${queued.length} ${queued.length === 1 ? "file" : "files"}`}
              </button>
            </div>
          ) : null}
        </section>

        {results.length > 0 ? (
          <section className="mt-6">
            <h2 className="text-sm font-semibold">
              {accepted} of {results.length} {results.length === 1 ? "file" : "files"} accepted
            </h2>
            <ul data-testid="results" className="mt-2 space-y-2 text-sm">
              {results.map((r, i) => (
                <li
                  key={`${r.file}-${i}`}
                  className={[
                    "rounded-lg border p-3",
                    r.ok
                      ? "border-emerald-200 bg-emerald-50"
                      : r.held
                        ? "border-amber-300 bg-amber-50"
                        : "border-red-200 bg-red-50",
                  ].join(" ")}
                >
                  <p className="font-medium">{r.file}</p>
                  {r.ok ? (
                    <p className="mt-0.5">
                      Received. It will appear in Purchase orders once DocFlow has read it.
                      {r.duplicateOf ? " This looks like one you already have — both are kept." : ""}
                    </p>
                  ) : (
                    <>
                      <p className="mt-0.5 font-medium">{r.error.title}</p>
                      <p className="mt-0.5">{r.error.message}</p>
                      <p className="mt-0.5 text-gray-700">{r.error.action}</p>
                    </>
                  )}
                </li>
              ))}
            </ul>
            {accepted > 0 ? (
              <Link href="/review" className="mt-3 inline-block text-sm text-blue-700 underline">
                Go to Purchase orders →
              </Link>
            ) : null}
          </section>
        ) : null}
      </main>
    </>
  );
}
