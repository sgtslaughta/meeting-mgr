import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Artifacts } from "../src/components/Artifacts";
import * as api from "../src/api";
import * as AuthCtx from "../src/AuthContext";

const meeting = {
  id: 7, title: "standup", status: "published" as const,
  current_stage: null, failed_stage: null, created_at: "",
  segments: [], clusters: [], attributions: [],
  key_topics: [{ id: 1, title: "budget", citations: [11, 12],
                 provenance: "inferred" as const }],
  minutes: [], action_items: [], decision_points: [],
};

describe("Artifacts", () => {
  it("renders each citation as a clickable target", () => {
    const onCiteClick = vi.fn();
    render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={() => {}} onCiteClick={onCiteClick} />);
    fireEvent.click(screen.getByRole("button", { name: "11" }));
    expect(onCiteClick).toHaveBeenCalledWith(11);
  });

  it("saves an edit and reports the change", async () => {
    const spy = vi.spyOn(api, "editArtifact").mockResolvedValue({} as never);
    const onChanged = vi.fn();
    render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={onChanged} onCiteClick={() => {}} />);
    fireEvent.change(screen.getByDisplayValue("budget"),
                     { target: { value: "budget and hiring" } });
    fireEvent.blur(screen.getByDisplayValue("budget and hiring"));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      7, "key_topics", 1, { title: "budget and hiring" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("warns before regenerating, since edits in that section are lost", async () => {
    const spy = vi.spyOn(api, "regenerate").mockResolvedValue({} as never);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(false));
    render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={() => {}} onCiteClick={() => {}} />);
    fireEvent.click(screen.getAllByRole("button", { name: /regenerate/i })[0]);
    expect(spy).not.toHaveBeenCalled();
  });

  it("deletes an item and reports the change", async () => {
    const spy = vi.spyOn(api, "deleteArtifact").mockResolvedValue(undefined);
    const onChanged = vi.fn();
    render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={onChanged} onCiteClick={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(7, "key_topics", 1));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("regenerates when the warning is confirmed", async () => {
    const spy = vi.spyOn(api, "regenerate").mockResolvedValue({} as never);
    const onChanged = vi.fn();
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
    render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={onChanged} onCiteClick={() => {}} />);
    fireEvent.click(screen.getAllByRole("button", { name: /regenerate/i })[0]);
    await waitFor(() => expect(spy).toHaveBeenCalledWith(7, "key_topics"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("does not send provenance or citations in an edit patch", async () => {
    const spy = vi.spyOn(api, "editArtifact").mockResolvedValue({} as never);
    render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={() => {}} onCiteClick={() => {}} />);
    fireEvent.change(screen.getByDisplayValue("budget"),
                     { target: { value: "renamed" } });
    fireEvent.blur(screen.getByDisplayValue("renamed"));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const patch = spy.mock.calls[0][3];
    expect(patch).not.toHaveProperty("provenance");
    expect(patch).not.toHaveProperty("citations");
  });

  it("polls after regenerating and stops once the section is no longer empty", async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(api, "regenerate").mockResolvedValue({} as never);
      vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
      const onChanged = vi.fn();
      const emptyMinutes = { ...meeting, minutes: [] };
      const { rerender } = render(
        <Artifacts meetingId={7} meeting={emptyMinutes}
                  onChanged={onChanged} onCiteClick={() => {}} />);

      await act(async () => {
        fireEvent.click(screen.getAllByRole("button", { name: /regenerate/i })[1]);
        await Promise.resolve();
      });
      expect(onChanged).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/regenerating/i)).toBeInTheDocument();

      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(onChanged).toHaveBeenCalledTimes(2);

      // Parent re-renders with the now-populated section, as it would after
      // onChanged() re-fetches the meeting.
      const filledMinutes = {
        ...meeting,
        minutes: [{ id: 9, text: "done", citations: [], provenance: "inferred" as const }],
      };
      rerender(<Artifacts meetingId={7} meeting={filledMinutes}
                          onChanged={onChanged} onCiteClick={() => {}} />);

      await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
      expect(onChanged).toHaveBeenCalledTimes(2);
      expect(screen.queryByText(/regenerating/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops polling on unmount so no timer outlives the component", async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(api, "regenerate").mockResolvedValue({} as never);
      vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
      const onChanged = vi.fn();
      const emptyMinutes = { ...meeting, minutes: [] };
      const { unmount } = render(
        <Artifacts meetingId={7} meeting={emptyMinutes}
                  onChanged={onChanged} onCiteClick={() => {}} />);

      await act(async () => {
        fireEvent.click(screen.getAllByRole("button", { name: /regenerate/i })[1]);
        await Promise.resolve();
      });
      expect(onChanged).toHaveBeenCalledTimes(1);

      unmount();
      await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
      expect(onChanged).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders an item with no citations without crashing", () => {
    const noCites = { ...meeting,
      key_topics: [{ id: 2, title: "no evidence", citations: [],
                     provenance: "unknown" as const }] };
    render(<Artifacts meetingId={7} meeting={noCites}
                      onChanged={() => {}} onCiteClick={() => {}} />);
    expect(screen.getByDisplayValue("no evidence")).toBeInTheDocument();
  });

  it("shows mutation controls for a member but hides them for an auditor", () => {
    // This is UX only — Task 10's server-side 403 is the actual enforcement.
    // Proving the hide/show split here still guards against the control
    // regressing to "always visible", which the backend test can't catch.
    const asRole = (role: "member" | "auditor") =>
      vi.spyOn(AuthCtx, "useAuth").mockReturnValue({
        account: { id: 1, email: "a@x.com", role, organization_id: 1 },
        loading: false, refresh: () => {},
      });

    asRole("member");
    const memberRender = render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={() => {}} onCiteClick={() => {}} />);
    expect(screen.getByRole("button", { name: /delete/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /regenerate/i }).length).toBeGreaterThan(0);
    expect(screen.getByDisplayValue("budget")).not.toBeDisabled();
    memberRender.unmount();

    asRole("auditor");
    render(<Artifacts meetingId={7} meeting={meeting}
                      onChanged={() => {}} onCiteClick={() => {}} />);
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: /regenerate/i }).length).toBe(0);
    expect(screen.getByDisplayValue("budget")).toBeDisabled();
  });
});
