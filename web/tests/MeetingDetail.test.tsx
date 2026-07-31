import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MeetingDetail } from "../src/routes/MeetingDetail";
import * as api from "../src/api";
import type { MeetingDetail as Detail } from "../src/types";

class FakeEventSource {
  static last: FakeEventSource;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor(public url: string) { FakeEventSource.last = this; }
  close() {}
}

// Segment ids are deliberately out of step with their array position: id 12
// sits at index 1, id 11 at index 0. A lookup that used the citation value
// as an array index (`segments[segmentId]`) would read past the end of a
// 2-element array and find nothing, instead of the id-11-shaped bug this
// guards against being masked by a coincidental index/id match.
const MEETING: Detail = {
  id: 5, title: "Test Meeting", status: "published",
  current_stage: null, failed_stage: null, created_at: "",
  segments: [
    { id: 11, start_seconds: 5, end_seconds: 10, text: "first", cluster_id: null },
    { id: 12, start_seconds: 42, end_seconds: 50, text: "second", cluster_id: null },
  ],
  clusters: [],
  attributions: [],
  key_topics: [{ id: 1, citations: [12], provenance: "confirmed", title: "Some topic" }],
  minutes: [], action_items: [], decision_points: [],
};

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/meetings/5"]}>
      <Routes>
        <Route path="/meetings/:id" element={<MeetingDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MeetingDetail onCiteClick", () => {
  afterEach(() => vi.restoreAllMocks());

  it("looks the segment up by database id, highlights it in the transcript, "
     + "and seeks the audio element to its start time", async () => {
    vi.spyOn(api, "getMeeting").mockResolvedValue(MEETING);
    vi.stubGlobal("EventSource", FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);

    renderDetail();
    await screen.findByText("Test Meeting");

    const citeButton = await screen.findByRole("button", { name: "12" });
    fireEvent.click(citeButton);

    const target = document.getElementById("segment-12");
    const other = document.getElementById("segment-11");
    await waitFor(() => expect(target?.className).toContain("highlighted"));
    expect(other?.className).not.toContain("highlighted");

    const player = document.getElementById("player") as HTMLAudioElement;
    expect(player.currentTime).toBe(42);
  });
});
