import { useEffect, useRef, useState } from "react";
import { deleteArtifact, editArtifact, regenerate } from "../api";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { useAuth } from "../AuthContext";
import type { Artifact, ArtifactType, MeetingDetail } from "../types";

const SECTIONS: { type: ArtifactType; heading: string; field: string }[] = [
  { type: "key_topics", heading: "Key Topics", field: "title" },
  { type: "minutes", heading: "Minutes", field: "text" },
  { type: "action_items", heading: "Action Items", field: "text" },
  { type: "decision_points", heading: "Decision Points", field: "text" },
];

// Regeneration deletes the section's rows then enqueues a Celery task; the
// task can take a while against a real LLM. Poll for the result rather than
// leaving the section looking permanently empty.
const POLL_INTERVAL_MS = 3000;
const POLL_CEILING_MS = 60000;

export function Artifacts({ meetingId, meeting, onChanged, onCiteClick }: {
  meetingId: number; meeting: MeetingDetail;
  onChanged: () => void; onCiteClick: (segmentId: number) => void;
}) {
  const { account } = useAuth();
  const canWrite = account?.role !== "auditor";
  const [regenerating, setRegenerating] =
    useState<Partial<Record<ArtifactType, boolean>>>({});
  const timers = useRef<Partial<Record<ArtifactType, ReturnType<typeof setInterval>>>>({});
  const elapsed = useRef<Partial<Record<ArtifactType, number>>>({});

  const stopPolling = (type: ArtifactType) => {
    const timer = timers.current[type];
    if (timer) clearInterval(timer);
    delete timers.current[type];
    delete elapsed.current[type];
    setRegenerating((r) => ({ ...r, [type]: false }));
  };

  // Stop polling any section that has come back non-empty.
  useEffect(() => {
    (Object.keys(timers.current) as ArtifactType[]).forEach((type) => {
      if ((meeting[type] as Artifact[]).length > 0) stopPolling(type);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meeting]);

  // Never leave a timer running past unmount.
  useEffect(() => () => {
    Object.values(timers.current).forEach((timer) => clearInterval(timer));
  }, []);

  return (
    <>
      {SECTIONS.map(({ type, heading, field }) => (
        <section key={type}>
          <h2>{heading}</h2>
          {canWrite && <button onClick={async () => {
            // Regenerating discards human edits in this section only — the
            // other three keep theirs. Say so before doing it.
            if (!confirm(`Regenerate ${heading}? Edits in this section are lost.`))
              return;
            await regenerate(meetingId, type);
            onChanged();
            elapsed.current[type] = 0;
            setRegenerating((r) => ({ ...r, [type]: true }));
            timers.current[type] = setInterval(() => {
              elapsed.current[type] = (elapsed.current[type] ?? 0) + POLL_INTERVAL_MS;
              if (elapsed.current[type]! >= POLL_CEILING_MS) {
                stopPolling(type);
                return;
              }
              onChanged();
            }, POLL_INTERVAL_MS);
          }}>Regenerate {heading}</button>}
          {regenerating[type] && <em> Regenerating…</em>}
          <ul>
            {(meeting[type] as Artifact[]).map((item) => (
              <li key={item.id}>
                <input defaultValue={String(item[field] ?? "")}
                       disabled={!canWrite}
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
                {canWrite && <button onClick={async () => {
                  await deleteArtifact(meetingId, type, item.id);
                  onChanged();
                }}>Delete</button>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
