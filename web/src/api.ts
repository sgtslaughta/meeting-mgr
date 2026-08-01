import type { ArtifactPatch, ArtifactType, MeetingDetail, MeetingSummary } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new ApiError(r.status, `${init?.method ?? "GET"} ${url} failed: ${r.status}`);
  return r.json() as Promise<T>;
}

const body = (data: unknown): RequestInit => ({
  method: "PATCH",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(data),
});

export const audioUrl = (id: number) => `/meetings/${id}/audio`;
export const eventsUrl = (id: number) => `/meetings/${id}/events`;

export const listMeetings = () => json<MeetingSummary[]>("/meetings");
export const getMeeting = (id: number) => json<MeetingDetail>(`/meetings/${id}`);

export async function uploadMeeting(title: string, file: File) {
  const form = new FormData();
  form.append("title", title);
  form.append("file", file);
  return json<{ meeting_id: number }>("/meetings", { method: "POST", body: form });
}

export const confirmCluster = (meetingId: number, clusterId: number,
                               name: string | null) =>
  json(`/meetings/${meetingId}/clusters/${clusterId}`,
       body({ participant_name: name }));

export const editArtifact = (meetingId: number, type: ArtifactType,
                             itemId: number, patch: ArtifactPatch) =>
  json(`/meetings/${meetingId}/${type}/${itemId}`, body(patch));

export async function deleteArtifact(meetingId: number, type: ArtifactType,
                                     itemId: number) {
  const r = await fetch(`/meetings/${meetingId}/${type}/${itemId}`,
                        { method: "DELETE" });
  if (!r.ok) throw new ApiError(r.status, `DELETE failed: ${r.status}`);
}

export const regenerate = (meetingId: number, type: ArtifactType) =>
  json(`/meetings/${meetingId}/regenerate/${type}`, { method: "POST" });

export async function startCapture(title: string) {
  const form = new FormData();
  form.append("title", title);
  return json<{ meeting_id: number; status: string }>("/meetings/capture", { method: "POST", body: form });
}

export async function uploadCaptureChunk(meetingId: number, seq: number, blob: Blob) {
  const form = new FormData();
  form.append("chunk", blob, `chunk-${seq}.webm`);
  const r = await fetch(`/meetings/${meetingId}/capture/chunks/${seq}`, { method: "PUT", body: form });
  if (!r.ok) throw new ApiError(r.status, `chunk ${seq} upload failed: ${r.status}`);
}

export const listCaptureChunks = (meetingId: number) =>
  json<{ seqs: number[] }>(`/meetings/${meetingId}/capture/chunks`);

export const finishCapture = (meetingId: number) =>
  json<{ meeting_id: number; status: string }>(`/meetings/${meetingId}/capture/finish`, { method: "POST" });
