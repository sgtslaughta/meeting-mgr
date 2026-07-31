import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { audioUrl, getMeeting } from "../api";
import { Artifacts } from "../components/Artifacts";
import { SpeakerReview } from "../components/SpeakerReview";
import { Transcript } from "../components/Transcript";
import { useProgress } from "../useProgress";
import type { MeetingDetail as Detail } from "../types";

export function MeetingDetail() {
  const meetingId = Number(useParams().id);
  const [meeting, setMeeting] = useState<Detail | null>(null);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const progress = useProgress(meetingId);

  const reload = useCallback(
    () => getMeeting(meetingId).then(setMeeting), [meetingId]);
  useEffect(() => { reload(); }, [reload]);
  // Refetch when the pipeline finishes so artifacts appear without a reload.
  useEffect(() => { if (progress.status === "published") reload(); },
            [progress.status, reload]);

  const onCiteClick = (segmentId: number) => {
    setHighlighted(segmentId);
    document.getElementById(`segment-${segmentId}`)
            ?.scrollIntoView({ behavior: "smooth", block: "center" });
    const segment = meeting?.segments.find((s) => s.id === segmentId);
    const player = document.getElementById("player") as HTMLAudioElement | null;
    if (segment && player) {
      player.currentTime = segment.start_seconds;
      player.play().catch(() => {});   // autoplay may be blocked; not fatal
    }
  };

  if (!meeting) return <main>Loading…</main>;

  return (
    <main>
      <h1>{meeting.title}</h1>
      {meeting.status !== "published" && (
        <p className="status">
          {progress.currentStage ? `${progress.currentStage}…` : meeting.status}
          {progress.failedStage && ` (failed at ${progress.failedStage})`}
        </p>
      )}
      <audio id="player" controls src={audioUrl(meetingId)} />
      <SpeakerReview meetingId={meetingId} clusters={meeting.clusters}
                     attributions={meeting.attributions} onConfirmed={reload} />
      <Artifacts meetingId={meetingId} meeting={meeting}
                 onChanged={reload} onCiteClick={onCiteClick} />
      <Transcript segments={meeting.segments} clusters={meeting.clusters}
                  attributions={meeting.attributions}
                  highlightedSegment={highlighted} />
    </main>
  );
}
