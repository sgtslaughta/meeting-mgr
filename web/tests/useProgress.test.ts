import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useProgress } from "../src/useProgress";

class FakeEventSource {
  static last: FakeEventSource;
  onmessage: ((e: { data: string }) => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeEventSource.last = this; }
  close() { this.closed = true; }
  emit(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}

describe("useProgress", () => {
  it("applies the opening snapshot then live transitions", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const { result } = renderHook(() => useProgress(7));

    act(() => FakeEventSource.last.emit(
      { status: "processing", current_stage: "diarize", failed_stage: null }));
    await waitFor(() => expect(result.current.currentStage).toBe("diarize"));

    act(() => FakeEventSource.last.emit({ stage: "transcribe", state: "started" }));
    await waitFor(() => expect(result.current.currentStage).toBe("transcribe"));
  });

  it("closes the stream once the meeting publishes", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const { result } = renderHook(() => useProgress(7));
    act(() => FakeEventSource.last.emit({ stage: "publish", state: "finished" }));
    await waitFor(() => expect(result.current.status).toBe("published"));
    expect(FakeEventSource.last.closed).toBe(true);
  });

  it("closes the stream on a live failed-stage event, not only on publish", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const { result } = renderHook(() => useProgress(7));
    act(() => FakeEventSource.last.emit({ stage: "transcribe", state: "failed" }));
    await waitFor(() => expect(result.current.status).toBe("failed"));
    expect(result.current.failedStage).toBe("transcribe");
    expect(FakeEventSource.last.closed).toBe(true);
  });

  it("closes the EventSource on unmount even if the pipeline has not finished", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const { unmount } = renderHook(() => useProgress(7));
    const source = FakeEventSource.last;
    expect(source.closed).toBe(false);
    unmount();
    expect(source.closed).toBe(true);
  });
});
