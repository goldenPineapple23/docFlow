-- DocFlow — Phase 3 (a viewable preview for formats a browser cannot render)
--
-- New columns: documents.preview_storage_path, documents.preview_media_type,
-- documents.preview_kind.
--
-- Section 7.11 deliberately accepts Word, Excel, raw email, RTF and TIFF --
-- "a format DocFlow rejects is a PO the customer has to key by hand, which
-- is the exact failure the product exists to prevent". No browser renders
-- any of them. The review screen's whole purpose is reading the original
-- beside the extracted values, so for those formats the most important
-- screen in the product was half blank, and the reviewer's only recourse was
-- to download the file and open it in another application.
--
-- The fix cannot live in the web process. Section 7.11 is unambiguous:
-- "Parsing never runs in the web process. All document parsing (PDF text
-- extraction, DOCX/XLSX/RTF reading, image decoding) runs in an isolated
-- worker." Converting a TIFF to a PNG is image decoding; reading a DOCX is
-- parsing. So the preview is produced where parsing already happens -- the
-- worker, or a seeding script -- written to storage like any other file, and
-- the API only ever serves bytes it did not decode.
--
-- Recorded as DECISIONS.md D-092.

alter table documents
    -- Where the viewable rendering lives, under the same tenants/{id}/ prefix
    -- as everything else (Section 7.5). NULL when the original is already
    -- viewable, or when no preview could be produced.
    add column preview_storage_path text,

    -- The media type to serve it as. Constrained to the handful a browser
    -- renders safely: never text/html or image/svg+xml, both of which can
    -- carry script, and rendering document-derived markup is exactly what
    -- Section 7.12 forbids.
    add column preview_media_type text
        check (preview_media_type is null or preview_media_type in
               ('image/png', 'image/jpeg', 'application/pdf', 'text/plain; charset=utf-8')),

    -- What the reviewer is actually looking at, so the screen can say so
    -- honestly. A converted image is still the page as it was sent; text
    -- extracted from a Word file is NOT the original layout, and a reviewer
    -- comparing values against it deserves to know which they have.
    add column preview_kind text
        check (preview_kind is null or preview_kind in ('converted_image', 'extracted_text'));
