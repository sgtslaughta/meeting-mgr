import { describe, expect, it, vi, beforeEach } from "vitest";
import { login, logout, me } from "../src/auth";
import { ApiError } from "../src/api";

describe("auth", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("login stores and returns the account on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id: 1, email: "a@x.com", role: "member", organization_id: 1 }),
    }));
    const account = await login("a@x.com", "secret");
    expect(account).toEqual({ id: 1, email: "a@x.com", role: "member", organization_id: 1 });
  });

  it("login throws an ApiError on bad credentials", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(login("a@x.com", "wrong")).rejects.toBeInstanceOf(ApiError);
  });

  it("me resolves null rather than throwing on a 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(me()).resolves.toBeNull();
  });

  it("logout followed by a session check reflects no account", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 204 })   // /auth/logout
      .mockResolvedValueOnce({ ok: false, status: 401 }); // /auth/me after logout
    vi.stubGlobal("fetch", fetchMock);
    await logout();
    await expect(me()).resolves.toBeNull();
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/auth/logout", { method: "POST" });
  });
});
