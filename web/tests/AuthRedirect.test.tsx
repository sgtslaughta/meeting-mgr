import { render, screen, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useAuthRedirect } from "../src/App";
import { ApiError } from "../src/api";

function Probe() {
  useAuthRedirect();
  const location = useLocation();
  return <div>path:{location.pathname}</div>;
}

function dispatch401() {
  const event = new Event("unhandledrejection") as PromiseRejectionEvent;
  Object.defineProperty(event, "reason", { value: new ApiError(401, "nope"), configurable: true });
  window.dispatchEvent(event);
}

describe("useAuthRedirect", () => {
  it("routes to /login when any api call rejects with a 401", () => {
    render(
      <MemoryRouter initialEntries={["/meetings/1"]}>
        <Routes><Route path="*" element={<Probe />} /></Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("path:/meetings/1")).toBeInTheDocument();
    act(() => dispatch401());
    expect(screen.getByText("path:/login")).toBeInTheDocument();
  });

  it("does not act again once already on /login, so login's own failure handling isn't fought over", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes><Route path="*" element={<Probe />} /></Routes>
      </MemoryRouter>,
    );
    act(() => dispatch401());
    expect(screen.getByText("path:/login")).toBeInTheDocument();
  });
});
