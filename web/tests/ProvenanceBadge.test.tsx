import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProvenanceBadge } from "../src/components/ProvenanceBadge";

describe("ProvenanceBadge", () => {
  it("shows inferred as visible text, not only a colour", () => {
    render(<ProvenanceBadge provenance="inferred" />);
    // A reader must be able to tell a guess from a decision without relying
    // on colour perception.
    expect(screen.getByText("inferred")).toBeInTheDocument();
  });

  it("exposes provenance as a data attribute for styling", () => {
    const { container } = render(<ProvenanceBadge provenance="confirmed" />);
    expect(container.querySelector("[data-provenance='confirmed']")).not.toBeNull();
  });

  it("explains what the state means", () => {
    render(<ProvenanceBadge provenance="inferred" />);
    expect(screen.getByTitle(/proposed by a model/i)).toBeInTheDocument();
  });
});
