import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SpeakerReview } from "../src/components/SpeakerReview";
import * as api from "../src/api";
import * as AuthCtx from "../src/AuthContext";

const clusters = [{ id: 1, label: "SPEAKER_00",
                    spans: [{ start: 0, end: 2 }, { start: 10, end: 30 }] }];
const attributions = [{ cluster_id: 1, participant_id: 5,
                        participant_name: "Sarah", provenance: "inferred" as const }];

describe("SpeakerReview", () => {
  it("prefills the proposed name and marks it inferred", () => {
    render(<SpeakerReview meetingId={7} clusters={clusters}
                          attributions={attributions} onConfirmed={() => {}} />);
    expect(screen.getByDisplayValue("Sarah")).toBeInTheDocument();
    expect(screen.getByText("inferred")).toBeInTheDocument();
  });

  it("seeks the sample clip to the cluster's longest span", () => {
    render(<SpeakerReview meetingId={7} clusters={clusters}
                          attributions={attributions} onConfirmed={() => {}} />);
    const audio = screen.getByTestId("sample-1") as HTMLAudioElement;
    // Longest span is 10-30s, not the first span — a two-second clip of
    // someone saying "yeah" identifies nobody.
    expect(audio.src).toContain("#t=10,30");
  });

  it("confirms the edited name and notifies the parent", async () => {
    const spy = vi.spyOn(api, "confirmCluster").mockResolvedValue({} as never);
    const onConfirmed = vi.fn();
    render(<SpeakerReview meetingId={7} clusters={clusters}
                          attributions={attributions} onConfirmed={onConfirmed} />);
    fireEvent.change(screen.getByDisplayValue("Sarah"),
                     { target: { value: "Sarah Chen" } });
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(7, 1, "Sarah Chen"));
    await waitFor(() => expect(onConfirmed).toHaveBeenCalled());
  });

  it("renders sensibly when a cluster has no attribution", () => {
    render(<SpeakerReview meetingId={7} clusters={clusters}
                          attributions={[]} onConfirmed={() => {}} />);
    expect(screen.getByPlaceholderText("who is this?")).toHaveValue("");
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("hides the Confirm control and disables the name field for an auditor", () => {
    // UX only — Task 10's server-side 403 is the real enforcement.
    vi.spyOn(AuthCtx, "useAuth").mockReturnValue({
      account: { id: 1, email: "a@x.com", role: "auditor", organization_id: 1 },
      loading: false, refresh: () => {},
    });
    render(<SpeakerReview meetingId={7} clusters={clusters}
                          attributions={attributions} onConfirmed={() => {}} />);
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("who is this?")).toBeDisabled();
  });
});
