import { useRef, useState } from "react";
import { finishCapture, listCaptureChunks, startCapture, uploadCaptureChunk } from "../api";

const TIMESLICE_MS = 5000;

export function useCaptureRecorder(onFinished: (meetingId: number) => void) {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const meetingIdRef = useRef<number | null>(null);
  const seqRef = useRef(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  // Chains uploads so chunks land in order and only one Blob is ever held
  // in memory at a time -- never the whole recording, client or server side.
  const pendingRef = useRef<Promise<void>>(Promise.resolve());

  async function start(title: string) {
    setError(null);
    try {
      const { meeting_id } = await startCapture(title);
      meetingIdRef.current = meeting_id;
      seqRef.current = 0;
      // Permission denial is a normal, expected outcome here (not a bug):
      // getUserMedia rejects with NotAllowedError/PermissionDeniedError when
      // the user declines the mic prompt. Surface it instead of leaving the
      // UI in limbo with a half-created (never-finished) capturing Meeting.
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      recorder.ondataavailable = (e) => {
        if (e.data.size === 0) return;
        const seq = seqRef.current++;
        pendingRef.current = pendingRef.current.then(() => uploadCaptureChunk(meeting_id, seq, e.data));
      };
      recorder.start(TIMESLICE_MS);
      recorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      const message =
        err instanceof Error && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError")
          ? "Microphone access was denied. Allow microphone access to record a meeting."
          : err instanceof Error
            ? err.message
            : "Could not start capture.";
      setError(message);
      meetingIdRef.current = null;
    }
  }

  async function stop() {
    recorderRef.current?.stop();
    setRecording(false);
    await pendingRef.current;
    const meetingId = meetingIdRef.current;
    if (meetingId != null) {
      await finishCapture(meetingId);
      onFinished(meetingId);
    }
  }

  // Resume after a reload/dropped connection: pick up sequencing after the
  // highest chunk the server already has, so nothing already accepted is
  // re-uploaded.
  async function resume(meetingId: number) {
    const { seqs } = await listCaptureChunks(meetingId);
    meetingIdRef.current = meetingId;
    seqRef.current = seqs.length ? Math.max(...seqs) + 1 : 0;
  }

  return { recording, start, stop, resume, error };
}
