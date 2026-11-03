# ADR 0004: No composite capabilities — answer-shaped APIs

Date: 2026-10-21
Status: Accepted

## Contents

- [Context](#context)
- [Decision](#decision)
- [Consequences](#consequences)

## Context

For a question like "returns of all accounts of a customer", the agent must
first find the customer's accounts, then the returns of each account.
Three designs were on the table:

1. **Composite capabilities** — declare multi-step pipelines in the
   registry YAML; our executor runs the fan-out and join.
2. **Model-orchestrated chaining** — keep capabilities atomic; the agent
   loop chains discover → execute → discover → execute.
3. **Answer-shaped APIs** — register capabilities at the granularity of the
   questions asked; the join/fan-out lives behind the API itself.

## Decision

No composition engine in our layer (option 1 is rejected). Capabilities are
registered at the granularity of the questions people ask. If answering
needs a join or a fan-out, the API behind the capability does it — e.g. the
registry holds `returns_by_customer`, not `accounts_by_customer` +
`account_returns` plus a pipeline gluing them.

`discover_capability` returns the single best-matching capability: the one
API needed to answer, not a grab-bag of primitives. Model-orchestrated
chaining (option 2) remains the fallback for genuinely multi-hop needs that
no single API covers — accepted as working for simple cases.

## Consequences

- Registry authors describe user needs ("returns for all accounts of a
  customer"), not backend tables. Descriptions are written so vector search
  lands on the one right API.
- Discovery is tuned for precision: prefer the single best match over
  several partial ones.
- There is no workflow/pipeline engine to build, test, version, or govern.
  Data logic stays behind the APIs that own the data.
- The approval story is unchanged: each registry entry is still one
  reviewed grant.
