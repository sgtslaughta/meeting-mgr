import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CaptureRecorder } from "../src/components/CaptureRecorder";

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(public stream: unknown, public opts: unknown) {
    FakeMediaRecorder.instances.push(this);
  }
  start(_timeslice: number) {}
  stop() {
    this.onstop?.();
  }
  emit(bytes: string) {
    this.ondataavailable?.({ data: new Blob([bytes]) });
  }
}

beforeEach(() => {
  FakeMediaRecorder.instances = [];
  // @ts-expect-error test double
  global.MediaRecorder = FakeMediaRecorder;
  global.navigator.mediaDevices = {
    // @ts-expect-error test double
    getUserMedia: vi.fn().mockResolvedValue({}),
  };
  global.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/meetings/capture") {
      return new Response(JSON.stringify({ meeting_id: 42, status: "capturing" }), { status: 201 });
    }
    if (url.includes("/capture/chunks/") && init?.method === "PUT") {
      return new Response(JSON.stringify({ seq: 0 }), { status: 200 });
    }
    if (url.endsWith("/capture/finish")) {
      return new Response(JSON.stringify({ meeting_id: 42, status: "pending" }), { status: 200 });
    }
    throw new Error(`unexpected fetch ${url}`);
  }) as typeof fetch;
});

function startClicked() {
  // The Start capture button is disabled until a title is entered (see
  // CaptureRecorder.tsx), so every test that needs a running capture must
  // fill the title field first -- the brief's reference test omitted this
  // and would hang forever waiting for a MediaRecorder instance that a
  // disabled button never creates.
  fireEvent.change(screen.getByPlaceholderText("title"), { target: { value: "Standup" } });
  fireEvent.click(screen.getByRole("button", { name: /start capture/i }));
}

describe("CaptureRecorder", () => {
  it("starts a capture, streams chunks in order, and finishes on stop", async () => {
    const onFinished = vi.fn();
    render(<CaptureRecorder onFinished={onFinished} />);

    startClicked();
    await waitFor(() => expect(FakeMediaRecorder.instances).toHaveLength(1));

    const recorder = FakeMediaRecorder.instances[0];
    act(() => recorder.emit("chunk-0"));
    act(() => recorder.emit("chunk-1"));

    // Chunks must reach the server WHILE capturing, not be buffered until
    // stop is clicked -- that's the whole point of chunked upload. Assert
    // the PUTs have landed before the stop button is ever pressed.
    await waitFor(() => {
      const putCalls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
        ([url]: [string]) => url.includes("/capture/chunks/"),
      );
      expect(putCalls.map(([url]: [string]) => url)).toEqual([
        "/meetings/42/capture/chunks/0",
        "/meetings/42/capture/chunks/1",
      ]);
    });
    expect((global.fetch as ReturnType<typeof vi.fn>).mock.calls.some(([url]: [string]) =>
      url.endsWith("/capture/finish"),
    )).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /stop/i }));

    await waitFor(() => expect(onFinished).toHaveBeenCalledWith(42));

    const putCalls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([url]: [string]) => url.includes("/capture/chunks/"),
    );
    expect(putCalls.map(([url]: [string]) => url)).toEqual([
      "/meetings/42/capture/chunks/0",
      "/meetings/42/capture/chunks/1",
    ]);
  });

  it("shows a clear message when microphone permission is denied", async () => {
    const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    global.navigator.mediaDevices = {
      // @ts-expect-error test double
      getUserMedia: vi.fn().mockRejectedValue(denied),
    };
    const onFinished = vi.fn();
    render(<CaptureRecorder onFinished={onFinished} />);

    startClicked();

    expect(await screen.findByRole("alert")).toHaveTextContent(/microphone access was denied/i);
    expect(FakeMediaRecorder.instances).toHaveLength(0);
    expect(screen.getByRole("button", { name: /start capture/i })).toBeInTheDocument();
  });
});
