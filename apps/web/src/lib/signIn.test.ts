import { describe, expect, it } from "vitest";
import { afterSignIn } from "./signIn";

describe("afterSignIn", () => {
  it("returns to the page that found the person signed out", () => {
    expect(afterSignIn("?next=%2Fteam")).toBe("/team");
    expect(afterSignIn("?next=%2Freview%3Fstatus%3Dneeds_review")).toBe("/review?status=needs_review");
  });

  it("goes home when there is nowhere to return to", () => {
    expect(afterSignIn("")).toBe("/");
    expect(afterSignIn("?next=")).toBe("/");
  });

  it("never leaves the site, whatever the link says", () => {
    expect(afterSignIn("?next=https%3A%2F%2Fevil.example")).toBe("/");
    expect(afterSignIn("?next=%2F%2Fevil.example")).toBe("/");
    expect(afterSignIn("?next=%2F%5Cevil.example")).toBe("/");
    expect(afterSignIn("?next=javascript%3Aalert(1)")).toBe("/");
  });
});
