import { useState } from "react";
import { useCaptureRecorder } from "../hooks/useCaptureRecorder";

export function CaptureRecorder({ onFinished }: { onFinished: (meetingId: number) => void }) {
  const [title, setTitle] = useState("");
  const { recording, start, stop, error } = useCaptureRecorder(onFinished);

  return (
    <div className="capture-recorder">
      <input value={title} placeholder="title" onChange={(e) => setTitle(e.target.value)}
             disabled={recording} />
      {recording ? (
        <button type="button" onClick={() => stop()}>Stop</button>
      ) : (
        <button type="button" disabled={!title} onClick={() => start(title)}>Start capture</button>
      )}
      {error && <p role="alert" className="capture-error">{error}</p>}
    </div>
  );
}
