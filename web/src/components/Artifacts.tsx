import { deleteArtifact, editArtifact, regenerate } from "../api";
import { ProvenanceBadge } from "./ProvenanceBadge";
import type { Artifact, ArtifactType, MeetingDetail } from "../types";

const SECTIONS: { type: ArtifactType; heading: string; field: string }[] = [
  { type: "key_topics", heading: "Key Topics", field: "title" },
  { type: "minutes", heading: "Minutes", field: "text" },
  { type: "action_items", heading: "Action Items", field: "text" },
  { type: "decision_points", heading: "Decision Points", field: "text" },
];

export function Artifacts({ meetingId, meeting, onChanged, onCiteClick }: {
  meetingId: number; meeting: MeetingDetail;
  onChanged: () => void; onCiteClick: (segmentId: number) => void;
}) {
  return (
    <>
      {SECTIONS.map(({ type, heading, field }) => (
        <section key={type}>
          <h2>{heading}</h2>
          <button onClick={async () => {
            // Regenerating discards human edits in this section only — the
            // other three keep theirs. Say so before doing it.
            if (!confirm(`Regenerate ${heading}? Edits in this section are lost.`))
              return;
            await regenerate(meetingId, type);
            onChanged();
          }}>Regenerate {heading}</button>
          <ul>
            {(meeting[type] as Artifact[]).map((item) => (
              <li key={item.id}>
                <input defaultValue={String(item[field] ?? "")}
                       onBlur={async (e) => {
                         if (e.target.value === String(item[field] ?? "")) return;
                         await editArtifact(meetingId, type, item.id,
                                            { [field]: e.target.value });
                         onChanged();
                       }} />
                <ProvenanceBadge provenance={item.provenance} />
                <span className="citations">
                  {item.citations.length === 0
                    ? <em>no evidence linked</em>
                    : item.citations.map((c) => (
                        <button key={c} onClick={() => onCiteClick(c)}>{c}</button>
                      ))}
                </span>
                <button onClick={async () => {
                  await deleteArtifact(meetingId, type, item.id);
                  onChanged();
                }}>Delete</button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
