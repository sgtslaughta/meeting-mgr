import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Login } from "../src/routes/Login";
import { AuthProvider } from "../src/AuthContext";

describe("Login", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows a generic error on failed login without revealing whether the email exists", async () => {
    // API returns the same 401 body whether the email is unknown or the
    // password is wrong — the UI must not attempt to tell those apart.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 401, text: async () => "unauthorized",
    }));
    render(<MemoryRouter><AuthProvider><Login /></AuthProvider></MemoryRouter>);
    fireEvent.change(screen.getByPlaceholderText("email"), { target: { value: "nobody@x.com" } });
    fireEvent.change(screen.getByPlaceholderText("password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(screen.getByRole("alert"))
      .toHaveTextContent("invalid email or password"));
  });

  it("logs in and refreshes account state on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id: 1, email: "a@x.com", role: "member", organization_id: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><AuthProvider><Login /></AuthProvider></MemoryRouter>);
    fireEvent.change(screen.getByPlaceholderText("email"), { target: { value: "a@x.com" } });
    fireEvent.change(screen.getByPlaceholderText("password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/auth/login",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ email: "a@x.com", password: "secret" }) }),
    ));
  });
});
