import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// No session: request() then sends no token, and nothing leaves the test.
vi.mock("@/lib/supabase", () => ({
  supabase: { auth: { getSession: async () => ({ data: { session: null } }) } },
}));

import { CatalogErrorBox } from "./CatalogErrorBox";
import { APP_000, APP_001 } from "@/lib/catalogMirror";
import { fieldLabel } from "@/lib/fieldLabels";
import { ReviewApiError, UNEXPECTED, request } from "@/lib/review";

const REQ_002 = {
  code: "REQ-002",
  title: "This email address can't receive mail",
  message: "This address can't receive email, so DocFlow didn't save anything.",
  action: "Check the part after the @ and try again.",
};

describe("the catalog error box (D-241)", () => {
  it("puts the refused field's label in front of the title", () => {
    render(<CatalogErrorBox error={{ ...REQ_002, detail: { fields: ["owner_email"] } }} />);
    const box = screen.getByTestId("catalog-error");
    expect(box.querySelector("p")?.textContent).toBe("Owner email: This email address can't receive mail");
    expect(box.textContent).toContain(REQ_002.message);
    expect(box.textContent).toContain(REQ_002.action);
  });

  it("names every refused field, in the order the API gave", () => {
    render(<CatalogErrorBox error={{ ...REQ_002, detail: { fields: ["owner_email", "contact_email"] } }} />);
    expect(screen.getByTestId("catalog-error").querySelector("p")?.textContent).toBe(
      "Owner email, Contact email: This email address can't receive mail",
    );
  });

  it("shows the title alone when the answer names no field", () => {
    render(<CatalogErrorBox error={REQ_002} />);
    expect(screen.getByTestId("catalog-error").querySelector("p")?.textContent).toBe(REQ_002.title);
  });

  it("ignores a detail that isn't a list of field names", () => {
    render(<CatalogErrorBox error={{ ...REQ_002, detail: { fields: "owner_email" } }} />);
    expect(screen.getByTestId("catalog-error").querySelector("p")?.textContent).toBe(REQ_002.title);
  });

  it("renders a label as text, never as markup", () => {
    render(<CatalogErrorBox error={{ ...REQ_002, detail: { fields: ["<b>x</b>"] } }} />);
    const title = screen.getByTestId("catalog-error").querySelector("p");
    expect(title?.querySelector("b")).toBeNull();
    expect(title?.textContent).toContain("<b>x</b>");
  });

  it("spells out a field it has no label for", () => {
    expect(fieldLabel("limit")).toBe("Limit");
    expect(fieldLabel("lines.0.unit_price")).toBe("Lines 0 unit price");
  });
});

describe("the fallbacks claim nothing the page can't know (D-241)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("are the catalog's APP-000 and APP-001", () => {
    expect(UNEXPECTED).toBe(APP_000);
    for (const entry of [APP_000, APP_001]) {
      const words = `${entry.title} ${entry.message} ${entry.action}`.toLowerCase();
      expect(words).not.toContain("alerted");
      expect(words).not.toContain("nothing was changed");
      expect(words).not.toContain("nothing was stored");
      expect(words).not.toContain("couldn't reach");
    }
  });

  it("is what a non-catalog answer shows, such as FastAPI's own 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: [{ loc: ["body", "x"], msg: "bad" }] }), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const failure = await request("/admin/tenants").catch((e) => e);
    expect(failure).toBeInstanceOf(ReviewApiError);
    expect((failure as ReviewApiError).catalog).toEqual(APP_000);
  });

  it("passes a catalog answer's fields through to the box", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: { ...REQ_002, detail: { fields: ["owner_email"] } } }), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const failure = (await request("/admin/tenants/new").catch((e) => e)) as ReviewApiError;
    expect(failure.catalog.code).toBe("REQ-002");
    expect(failure.catalog.detail).toEqual({ fields: ["owner_email"] });
  });
});
