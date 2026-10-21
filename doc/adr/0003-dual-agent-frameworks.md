# ADR 0003: Switchable agent frameworks (Strands / LangGraph)

**Status:** accepted
**Date:** 2026-10-21
**Context:** the agent loop is currently Strands-only. LangGraph is worth
evaluating (richer ecosystem: interrupts, human-in-the-loop, Studio), and the
user wants to switch frameworks at any point via config.

**Decision:** support both frameworks behind the unchanged `ModelGateway`
port.

- Framework = the agent loop (Strands `Agent` vs LangGraph ReAct agent).
  Provider = the LLM (Ollama vs Gemini). The two axes are independent.
- One shared base per framework: `adapters/strands_base.py` (exists),
  `adapters/langgraph_base.py` (new). Each holds the loop, streaming event
  translation, retries, and error translation. Tiny per-provider leaves
  (`...OllamaGateway`, `...GeminiGateway`) provide model construction,
  transient-error classification, and `ping()`.
- Tools are defined once as Strands `@tool` functions (the canonical form).
  `LangGraphGatewayBase` translates each into a LangChain `StructuredTool`
  (name, description, JSON schema from the Strands tool spec) at wiring
  time. No tool is rewritten per framework.
- `main.py` resolves the framework × provider matrix from `AGENT_FRAMEWORK`
  × `AGENT_MODEL_PROVIDER`. The service only ever sees a `ModelGateway`.
- LangGraph runs stateless per call (no checkpointer), matching the Strands
  side; history stays owned by the `ConversationRepository`.

**Rationale:**
- The port made this cheap: the framework is an adapter detail, so trying
  LangGraph is a spike, not a commitment. If it doesn't earn its place, two
  files get deleted.
- Keeping both switchable costs little (the matrix is resolved in one place)
  and lets real workloads decide.

**Consequences:**
- New dependencies: `langchain`, `langgraph`, `langchain-core`,
  `langchain-ollama`, `langchain-google-genai`.
- Two runtimes to keep in behavioral parity — each framework gets its own
  live integration test (`tests/adapters/test_*_gateway.py`).
- `create_react_agent` is deprecated in LangGraph v1 (moved to
  `langchain.agents.create_agent`); the adapter uses the new import.
- Embeddings stay on the provider path (`OllamaEmbeddingGateway` /
  `GeminiEmbeddingGateway`) regardless of framework — discovery is not
  framework-specific.
