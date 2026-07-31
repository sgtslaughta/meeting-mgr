import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Artifacts } from "../src/components/Artifacts";
import * as api from "../src/api";

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

  it("renders an item with no citations without crashing", () => {
    const noCites = { ...meeting,
      key_topics: [{ id: 2, title: "no evidence", citations: [],
                     provenance: "unknown" as const }] };
    render(<Artifacts meetingId={7} meeting={noCites}
                      onChanged={() => {}} onCiteClick={() => {}} />);
    expect(screen.getByDisplayValue("no evidence")).toBeInTheDocument();
  });
});
