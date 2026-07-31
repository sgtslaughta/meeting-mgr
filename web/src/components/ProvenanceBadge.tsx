import type { Provenance } from "../types";

const EXPLANATION: Record<Provenance, string> = {
  inferred: "Proposed by a model. Nobody has checked this yet.",
  confirmed: "Confirmed by a person.",
  unknown: "Undecided.",
};

export function ProvenanceBadge({ provenance }: { provenance: Provenance }) {
  return (
    <span className="provenance" data-provenance={provenance}
          title={EXPLANATION[provenance]}>
      {provenance}
    </span>
  );
}
