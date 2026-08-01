import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listMeetings, uploadMeeting } from "../api";
import { CaptureRecorder } from "../components/CaptureRecorder";
import type { MeetingSummary } from "../types";

export function MeetingList() {
  const [meetings, setMeetings] = useState<MeetingSummary[]>([]);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const reload = () => listMeetings().then(setMeetings);
  useEffect(() => { reload(); }, []);

  return (
    <main>
      <h1>Meetings</h1>
      <form onSubmit={async (e) => {
        e.preventDefault();
        if (!file) return;
        await uploadMeeting(title, file);
        setTitle(""); setFile(null);
        reload();
      }}>
        <input value={title} placeholder="title"
               onChange={(e) => setTitle(e.target.value)} required />
        <input type="file" accept="audio/*,video/*"
               onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
        <button type="submit">Upload</button>
      </form>
      <CaptureRecorder onFinished={reload} />
      <ul>
        {meetings.map((m) => (
          <li key={m.id}>
            <Link to={`/meetings/${m.id}`}>{m.title}</Link>
            <span className="status">
              {m.status === "processing" && m.current_stage
                ? `${m.current_stage}…` : m.status}
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}
