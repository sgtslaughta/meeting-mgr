import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Transcript } from "../src/components/Transcript";

const segments = [
  { id: 11, start_seconds: 0, end_seconds: 2, text: "hello", cluster_id: 1 },
  { id: 12, start_seconds: 2, end_seconds: 4, text: "orphan", cluster_id: null },
];
const clusters = [{ id: 1, label: "SPEAKER_00", spans: [] }];

describe("Transcript", () => {
  it("shows the confirmed participant name over the raw label", () => {
    render(<Transcript segments={segments} clusters={clusters}
      attributions={[{ cluster_id: 1, participant_id: 5,
                       participant_name: "Sarah", provenance: "confirmed" }]}
      highlightedSegment={null} />);
    expect(screen.getByText(/Sarah/)).toBeInTheDocument();
  });

  it("falls back to the cluster label when nobody is attributed", () => {
    render(<Transcript segments={segments} clusters={clusters}
                       attributions={[]} highlightedSegment={null} />);
    expect(screen.getByText(/SPEAKER_00/)).toBeInTheDocument();
  });

  it("labels an unaligned segment UNKNOWN rather than guessing", () => {
    render(<Transcript segments={segments} clusters={clusters}
                       attributions={[]} highlightedSegment={null} />);
    expect(screen.getByText(/UNKNOWN/)).toBeInTheDocument();
  });

  it("gives every segment a stable anchor id and highlights the cited one", () => {
    const { container } = render(
      <Transcript segments={segments} clusters={clusters}
                  attributions={[]} highlightedSegment={11} />);
    expect(container.querySelector("#segment-11")).not.toBeNull();
    expect(container.querySelector("#segment-11")?.className)
      .toContain("highlighted");
    expect(container.querySelector("#segment-12")?.className)
      .not.toContain("highlighted");
  });
});
