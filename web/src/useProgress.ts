import { useEffect, useState } from "react";
import { eventsUrl } from "./api";

interface Progress {
  status: string | null;
  currentStage: string | null;
  failedStage: string | null;
}

export function useProgress(meetingId: number): Progress {
  const [progress, setProgress] = useState<Progress>({
    status: null, currentStage: null, failedStage: null,
  });

  useEffect(() => {
    const source = new EventSource(eventsUrl(meetingId));
    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if ("status" in data) {
        // Opening snapshot: whatever we missed before connecting.
        setProgress({ status: data.status, currentStage: data.current_stage,
                      failedStage: data.failed_stage });
        if (data.status === "published" || data.status === "failed") source.close();
        return;
      }
      setProgress((prev) => ({
        ...prev,
        currentStage: data.stage,
        failedStage: data.state === "failed" ? data.stage : prev.failedStage,
        status: data.stage === "publish" && data.state === "finished"
          ? "published" : data.state === "failed"
          ? "failed" : prev.status,
      }));
      if ((data.stage === "publish" && data.state === "finished") ||
          data.state === "failed") source.close();
    };
    return () => source.close();
  }, [meetingId]);

  return progress;
}
