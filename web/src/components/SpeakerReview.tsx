import { useState } from "react";
import { audioUrl, confirmCluster } from "../api";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { useAuth } from "../AuthContext";
import type { AttributionView, Cluster, Span } from "../types";

function longestSpan(spans: Span[]): Span | null {
  if (!spans.length) return null;
  return spans.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a));
}

export function SpeakerReview({ meetingId, clusters, attributions, onConfirmed }: {
  meetingId: number; clusters: Cluster[]; attributions: AttributionView[];
  onConfirmed: () => void;
}) {
  const { account } = useAuth();
  const canWrite = account?.role !== "auditor";
  const byCluster = new Map(attributions.map((a) => [a.cluster_id, a]));
  const [names, setNames] = useState<Record<number, string>>(
    Object.fromEntries(clusters.map((c) =>
      [c.id, byCluster.get(c.id)?.participant_name ?? ""])),
  );

  return (
    <section>
      <h2>Speakers</h2>
      {clusters.map((c) => {
        const span = longestSpan(c.spans);
        const attribution = byCluster.get(c.id);
        return (
          <div key={c.id} className="speaker-row">
            <span className="label">{c.label}</span>
            {span && (
              <audio controls data-testid={`sample-${c.id}`}
                     src={`${audioUrl(meetingId)}#t=${span.start},${span.end}`} />
            )}
            <input value={names[c.id] ?? ""}
                   placeholder="who is this?"
                   disabled={!canWrite}
                   onChange={(e) =>
                     setNames({ ...names, [c.id]: e.target.value })} />
            <ProvenanceBadge provenance={attribution?.provenance ?? "unknown"} />
            {canWrite && <button onClick={async () => {
              await confirmCluster(meetingId, c.id, names[c.id] || null);
              onConfirmed();
            }}>Confirm</button>}
          </div>
        );
      })}
    </section>
  );
}
