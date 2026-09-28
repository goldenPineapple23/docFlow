import { afterEach, describe, expect, it, vi } from "vitest";
import { registerStepUpHandler, stepUpRefusal, withStepUp } from "./stepUp";

const AUTH_007 = {
  code: "AUTH-007",
  title: "Confirm with your authenticator code",
  message: "m",
  action: "a",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let unregister: (() => void) | null = null;
afterEach(() => {
  unregister?.();
  unregister = null;
});

describe("the Console step-up (D-177)", () => {
  it("recognises only a 403 carrying AUTH-007", async () => {
    expect(await stepUpRefusal(json(403, { detail: AUTH_007 }))).toEqual(AUTH_007);
    expect(await stepUpRefusal(json(403, { detail: { code: "AUTH-006" } }))).toBeNull();
    expect(await stepUpRefusal(json(409, { detail: AUTH_007 }))).toBeNull();
    expect(await stepUpRefusal(new Response("not json", { status: 403 }))).toBeNull();
  });

  it("asks for a code and retries once when an action is refused with AUTH-007", async () => {
    const handler = vi.fn().mockResolvedValue(true);
    unregister = registerStepUpHandler(handler);
    const retry = vi.fn().mockResolvedValue(json(200, { status: "deleted" }));

    const result = await withStepUp(json(403, { detail: AUTH_007 }), retry);

    expect(handler).toHaveBeenCalledWith(AUTH_007);
    expect(retry).toHaveBeenCalledTimes(1);
    expect(result.status).toBe(200);
  });

  it("returns the refusal unchanged -- still readable -- when the code is cancelled", async () => {
    unregister = registerStepUpHandler(vi.fn().mockResolvedValue(false));
    const retry = vi.fn();

    const result = await withStepUp(json(403, { detail: AUTH_007 }), retry);

    expect(retry).not.toHaveBeenCalled();
    expect(result.status).toBe(403);
    expect((await result.json()).detail.code).toBe("AUTH-007");
  });

  it("leaves every other response alone, and does nothing outside the Console", async () => {
    const handler = vi.fn();
    unregister = registerStepUpHandler(handler);
    const retry = vi.fn();
    expect((await withStepUp(json(200, {}), retry)).status).toBe(200);
    expect((await withStepUp(json(403, { detail: { code: "AUTH-006" } }), retry)).status).toBe(403);
    expect(handler).not.toHaveBeenCalled();

    unregister();
    unregister = null;
    expect((await withStepUp(json(403, { detail: AUTH_007 }), retry)).status).toBe(403);
    expect(retry).not.toHaveBeenCalled();
  });
});
