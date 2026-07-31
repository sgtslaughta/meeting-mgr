import { describe, expect, it, vi, beforeEach } from "vitest";
import * as api from "../src/api";

describe("api client", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("builds a same-origin audio url", () => {
    expect(api.audioUrl(7)).toBe("/meetings/7/audio");
  });

  it("sends a null name to clear an attribution", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ cluster_id: 3, participant_id: null }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await api.confirmCluster(7, 3, null);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/meetings/7/clusters/3");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ participant_name: null });
  });

  it("throws on a non-ok response rather than returning undefined", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 404, text: async () => "not found",
    }));
    await expect(api.getMeeting(1)).rejects.toThrow(/404/);
  });
});
