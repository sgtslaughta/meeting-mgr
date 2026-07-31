import type { AttributionView, Cluster, Segment } from "../types";

export function Transcript({ segments, clusters, attributions, highlightedSegment }: {
  segments: Segment[]; clusters: Cluster[]; attributions: AttributionView[];
  highlightedSegment: number | null;
}) {
  const labels = new Map(clusters.map((c) => [c.id, c.label]));
  const names = new Map(
    attributions.filter((a) => a.participant_name)
                .map((a) => [a.cluster_id, a.participant_name as string]),
  );

  const speaker = (clusterId: number | null) => {
    if (clusterId === null) return "UNKNOWN";
    return names.get(clusterId) ?? labels.get(clusterId) ?? "UNKNOWN";
  };

  return (
    <section>
      <h2>Transcript</h2>
      <ol className="transcript">
        {segments.map((seg) => (
          <li key={seg.id} id={`segment-${seg.id}`}
              className={highlightedSegment === seg.id ? "highlighted" : ""}>
            <span className="speaker">{speaker(seg.cluster_id)}</span>
            <span className="text">{seg.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
