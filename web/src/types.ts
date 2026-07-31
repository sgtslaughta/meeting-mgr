export type Provenance = "inferred" | "confirmed" | "unknown";

export interface Segment {
  id: number;
  start_seconds: number;
  end_seconds: number;
  text: string;
  cluster_id: number | null;
}

export interface Artifact {
  id: number;
  citations: number[];
  provenance: Provenance;
  [key: string]: unknown;
}

export interface MeetingSummary {
  id: number;
  title: string;
  status: "pending" | "processing" | "published" | "failed";
  current_stage: string | null;
  failed_stage: string | null;
  created_at: string;
}

export interface Cluster { id: number; label: string; spans: Span[] }
export interface Span { start: number; end: number }

export interface AttributionView {
  cluster_id: number;
  participant_id: number | null;
  participant_name: string | null;
  provenance: Provenance;
}

export interface MeetingDetail extends MeetingSummary {
  segments: Segment[];
  clusters: Cluster[];
  attributions: AttributionView[];
  key_topics: Artifact[];
  minutes: Artifact[];
  action_items: Artifact[];
  decision_points: Artifact[];
}

export type ArtifactType =
  | "key_topics" | "minutes" | "action_items" | "decision_points";

// Server-owned fields on artifacts. The PATCH endpoint allowlists editable
// fields and 400s on anything else; `provenance`/`citations` are never
// client-writable, so the patch body type must not offer them.
export interface ArtifactPatch {
  [key: string]: unknown;
  provenance?: never;
  citations?: never;
}
