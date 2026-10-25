# ADR 0005: Proxy env handling lives in the composition root

**Status:** accepted
**Date:** 2026-10-25
**Context:** the Ollama adapters used to delete proxy env vars at import
time — the only environment access outside `config/settings.py`, and a
global side effect triggered by an import. The dev sandbox's proxy values
are malformed (httpx rejects them while parsing `no_proxy`), and the
`ollama` package builds an HTTP client at import time
(`ollama/__init__.py` creates a module-level client), so importing it
with those values present crashes before any model call is made.

**Decision:** proxy handling moves to the composition root
(`composition.build_gateway()`), which strips proxy env vars and then
lazily imports the gateway classes. Adapters never touch `os.environ`
again. Per-call Ollama clients additionally pass `trust_env=False`
(Strands `ollama_client_args`, LangChain `client_kwargs`), matching the
existing `httpx.Client(trust_env=False)` precedent for localhost calls.

**Why not eliminate the mutation outright:** `trust_env=False` only
covers clients constructed with it — it cannot fix the `ollama`
package's import-time client, which is built with default settings the
moment the package is imported. Eliminating the strip would require
patching or vendoring the `ollama` package, which is out of proportion
to a sandbox quirk. The strip is therefore kept, but confined to the
one place that already owns process-level wiring, documented here as
the sanctioned exception to "nothing else reads the environment".

**Consequences:**
- Importing an Ollama gateway module directly (outside the composed
  app) no longer strips proxy vars; in an environment with malformed
  proxy values, the first model build (`_build_model` /
  `_build_chat_model`) will fail at the `ollama` import instead. In
  normal environments this changes nothing.
- Gateway classes are lazily imported inside `build_gateway()`; the
  composition module's top level imports no gateway classes.
