import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AuthProvider, useAuth } from "../src/AuthContext";

function Probe() {
  const { account, loading } = useAuth();
  if (loading) return <div>loading</div>;
  return <div>{account ? `hello ${account.email}` : "anonymous"}</div>;
}

describe("AuthContext", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("resolves to anonymous on a 401 from /auth/me", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("anonymous")).toBeInTheDocument());
  });

  it("exposes the account on a successful /auth/me", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id: 1, email: "a@x.com", role: "member", organization_id: 1 }),
    }));
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByText("hello a@x.com")).toBeInTheDocument());
  });
});
