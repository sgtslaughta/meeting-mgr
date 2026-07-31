# Diarization runs as a separate service we own

Unlike transcription and summarization, speaker diarization has no de facto
standard HTTP API, so [ADR-0001](./0001-inference-over-http.md) cannot cover it.
Rather than import `pyannote` into the API — which would drag CUDA into the main
image and pin the app to the GPU box — diarization runs in its own container
behind a small HTTP API of our own definition, returning Speaker Clusters and
voice embeddings.

## Consequences

- This is the only ML dependency the project ships and versions itself.
- It is also the only component with a hard GPU affinity; operators can run it on
  a different machine from the API.
- The endpoint returns voice embeddings even though v1 only does manual
  attribution. Storing them now is what makes automatic voiceprint matching a
  later feature rather than a re-processing job over the entire archive.
- Voice embeddings are biometric data under GDPR Art. 9. Retaining them is a
  deliberate choice with a compliance cost, taken because re-deriving them later
  would require re-processing every Recording.
