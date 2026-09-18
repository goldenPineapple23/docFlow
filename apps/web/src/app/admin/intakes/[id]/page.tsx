"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getIntake, uploadIntakeFile, type Intake } from "@/lib/admin";
import { ReviewApiError, type CatalogError } from "@/lib/review";
import { CatalogErrorBox } from "@/components/admin/CatalogErrorBox";

/**
 * One intake: its files, and the upload into staging. Every file passes the
 * same checks as a customer upload (Section 7.11) before it is stored; a
 * rejected file is reported with its catalog code and stored nowhere.
 */
export default function IntakePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [intake, setIntake] = useState<Intake | null>(null);
  const [error, setError] = useState<CatalogError | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);

  const refresh = useCallback(async () => {
    setIntake((await getIntake(id)).intake);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    getIntake(id)
      .then(({ intake: row }) => {
        if (!cancelled) setIntake(row);
      })
      .catch((e) => {
        if (!cancelled && e instanceof ReviewApiError) setError(e.catalog);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function upload(files: File[]) {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    for (const file of files) {
      try {
        await uploadIntakeFile(id, file);
      } catch (err) {
        if (err instanceof ReviewApiError) {
          setError({ ...err.catalog, title: `${file.name}: ${err.catalog.title}` });
        }
      }
    }
    await refresh();
    setUploading(false);
  }

  if (intake === null) {
    return error ? <CatalogErrorBox error={error} /> : <p className="text-sm text-gray-500">Loading…</p>;
  }

  const linked = intake.linked_tenant_id !== null;

  const fileList = (
    <>
      <h3 className="mt-5 text-sm font-semibold">Uploaded ({intake.files.length})</h3>
      <ul data-testid="intake-files" className="mt-2 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white text-sm">
        {intake.files.map((f) => (
          <li key={f.id} className="flex flex-wrap justify-between gap-2 px-4 py-2">
            <span className="font-medium">{f.original_filename}</span>
            <span className="text-gray-500">
              {f.detected_type} · {(f.byte_size / 1024).toFixed(1)} KB · sha256 {f.sha256.slice(0, 12)}…
            </span>
          </li>
        ))}
        {intake.files.length === 0 ? <li className="px-4 py-2 text-gray-500">Nothing uploaded yet.</li> : null}
      </ul>
    </>
  );

  return (
    <>
      <Link href="/admin/intakes" className="text-sm text-blue-700 underline">
        ← Intakes
      </Link>
      <h1 className="mt-1 text-xl font-semibold">{intake.prospect_name}</h1>
      <p className="text-sm text-gray-600">
        {intake.contact_email ?? "No contact email"} · received{" "}
        {new Date(intake.received_at).toLocaleString()}
      </p>
      {intake.notes ? <p className="mt-2 text-sm">{intake.notes}</p> : null}

      {linked ? (
        <p className="mt-4 rounded border border-green-200 bg-green-50 p-3 text-sm">
          These files now belong to{" "}
          <Link href={`/admin/tenants/${intake.linked_tenant_id}`} className="font-medium text-blue-700 underline">
            the tenant created from this intake
          </Link>
          .
        </p>
      ) : (
        <>
          {/*
            Step 1 is the drop area, and it is the most prominent thing on
            the page. It used to be the browser's bare file input -- plain
            "Choose Files" text -- above a big dark "Create the tenant"
            button, and the founder went straight past it twice (D-106).
          */}
          <section className="mt-5 rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold">1. Add the prospect&apos;s files</h2>
            <p className="mt-1 text-sm text-gray-600">
              The catalog, the customer list if they sent one, and 5–10 sample purchase orders.
            </p>
            <label
              htmlFor="intake-files-picker"
              data-testid="intake-drop-zone"
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                void upload(Array.from(e.dataTransfer.files));
              }}
              className={[
                "mt-3 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors",
                dragging ? "border-slate-700 bg-slate-100" : "border-slate-300 bg-slate-50 hover:bg-slate-100",
              ].join(" ")}
            >
              <span className="rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white">
                {uploading ? "Uploading…" : "Choose files…"}
              </span>
              <span className="text-sm text-gray-600">or drag them here. You can pick several at once.</span>
              <input
                id="intake-files-picker"
                type="file"
                multiple
                disabled={uploading}
                data-testid="intake-file-input"
                className="sr-only"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  e.target.value = "";
                  void upload(files);
                }}
              />
            </label>
            {error ? <div className="mt-3"><CatalogErrorBox error={error} /></div> : null}
            {fileList}
          </section>

          <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5">
            <h2 className="text-sm font-semibold">2. Create the tenant</h2>
            <p className="mt-1 text-sm text-gray-600">
              {intake.files.length > 0
                ? `The ${intake.files.length} uploaded file${intake.files.length === 1 ? "" : "s"} will move into the new tenant.`
                : "Add the files first. You can still create the tenant without any if that's what you mean to do."}
            </p>
            <Link
              href={`/admin/tenants/new?intake=${id}`}
              data-testid="create-tenant-from-intake"
              className={[
                "mt-3 inline-block rounded px-4 py-2 text-sm font-medium",
                intake.files.length > 0
                  ? "bg-slate-900 text-white"
                  : "border border-gray-300 text-gray-600 hover:bg-gray-50",
              ].join(" ")}
            >
              Create the tenant from this intake →
            </Link>
          </section>
        </>
      )}

      {linked && error ? <div className="mt-4"><CatalogErrorBox error={error} /></div> : null}
      {linked ? fileList : null}
    </>
  );
}
