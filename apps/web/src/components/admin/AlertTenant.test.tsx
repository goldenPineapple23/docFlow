import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AlertTenant } from "./AlertTenant";
import type { FounderAlert } from "@/lib/admin";

const alert = (over: Partial<FounderAlert>): FounderAlert => ({
  id: "a1",
  type: "review_backlog",
  severity: "warning",
  tenant_id: "t1",
  tenant_name: "Acme Test Distributor",
  payload: {},
  created_at: "2026-10-21T03:15:00Z",
  ...over,
});

describe("who an alert on the Console home is about (D-214)", () => {
  it("links a tenant's alert to the tenant's page", () => {
    render(<AlertTenant alert={alert({})} />);
    const link = screen.getByRole("link", { name: "Acme Test Distributor" });
    expect(link.getAttribute("href")).toBe("/admin/tenants/t1");
  });

  it("links a staged intake's 'No tenant' to the intake list", () => {
    render(<AlertTenant alert={alert({ type: "staging_ttl_elapsed", tenant_id: null, tenant_name: null })} />);
    const link = screen.getByRole("link", { name: "No tenant" });
    expect(link.getAttribute("href")).toBe("/admin/intakes");
  });

  it("leaves every other tenant-less alert as plain text", () => {
    render(<AlertTenant alert={alert({ type: "rollup_stale", tenant_id: null, tenant_name: null })} />);
    expect(screen.getByText("No tenant")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
