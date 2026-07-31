# Inference is HTTP, not a library

Meeting-MGR calls OpenAI-compatible HTTP endpoints for both language and
transcription work, and ships no models of its own. The wire format is the
abstraction — vLLM, llama.cpp, Ollama, LM Studio, LiteLLM and the commercial
APIs all speak it — so "which model" is operator configuration (`base_url`,
`api_key`, `model`) rather than a provider interface in our code. We chose this
so the application runs identically on a laptop and a GPU server, and so a
self-hosted deployment stays genuinely self-hosted without us bundling weights.

## Consequences

- The API image carries no `torch` and assumes no GPU.
- No `LLMProvider` abstraction exists, and none should be added — the OpenAI
  schema already is one. A second interface layer over it would have exactly one
  real implementation.
- Every inference call is a **network call that can lie**: it may time out,
  rate-limit, or return malformed JSON. All structured calls validate against a
  Pydantic schema and retry with bounded backoff.
- Summarization quality becomes a deployment property, not a code property. Two
  instances of Meeting-MGR can produce materially different Meeting Records.
