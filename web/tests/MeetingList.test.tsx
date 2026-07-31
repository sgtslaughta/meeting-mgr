import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { MeetingList } from "../src/routes/MeetingList";
import * as api from "../src/api";

describe("MeetingList", () => {
  it("shows the running stage while a meeting is processing", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue([
      { id: 1, title: "standup", status: "processing",
        current_stage: "transcribe", failed_stage: null, created_at: "" },
    ]);
    render(<MemoryRouter><MeetingList /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/transcribe/)).toBeInTheDocument());
  });

  it("links each meeting to its detail page", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue([
      { id: 42, title: "standup", status: "published",
        current_stage: null, failed_stage: null, created_at: "" },
    ]);
    render(<MemoryRouter><MeetingList /></MemoryRouter>);
    await waitFor(() => expect(
      screen.getByRole("link", { name: /standup/ })).toHaveAttribute(
        "href", "/meetings/42"));
  });
});
