# ADR 0001: Tools in a top-level `tools/` folder

**Status:** accepted
**Date:** 2026-10-21
**Context:** `doc/architecture-reference.md` §11 (placement test) would put
technology-independent tool functions in `domain/`, and §2 says not to invent
categories.

**Decision:** tools live in a top-level `tools/` package
(`src/agent_api/tools/<name>/tool.py`), auto-discovered, instead of
`domain/plugins/`.

**Rationale:**
- The reference's own Agent profile (§13) names **model / tool / memory** as
  the agent's peer building blocks. Model and memory already live outside
  domain (adapters, ports); tools are the agent's third peer, not just
  another business rule.
- Predictability (the doc's governing principle): a contributor adding a
  tool looks for `tools/` first. `domain/plugins/` required knowing the
  classification rationale.
- Dependency direction is unchanged: adapters and `main.py` import from
  `tools/`; `tools/` imports nothing from the app. If a future tool needs
  outbound I/O, it goes through a core-owned port (§4), same as before.

**Consequences:** one more top-level folder than the default structure;
placement documented here instead of silently diverging.
