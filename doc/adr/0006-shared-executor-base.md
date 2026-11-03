# ADR 0006: Shared executor base subclasses its port

Date: 2026-10-25

## Contents

- [Context](#context)
- [Decision](#decision)
- [Consequences](#consequences)

## Context

ADR 0003 sanctions one shared base per agent framework
(`StrandsGatewayBase`, `LangGraphGatewayBase`) holding the loop/stream/
retry mechanics, with tiny per-provider leaves. The SQL executors grew
the analogous pattern — `SqlExecutorBase` holding read-only enforcement,
parameterized execution, row caps, and retries, with per-driver leaves
(`SnowflakeCapabilityExecutor`, `SqlServerCapabilityExecutor`) — but it
was never recorded, and none of the three bases subclassed its port:
conformance was duck-typed, so signature drift in a base or leaf was
caught only at runtime (code review m-2).

## Decision

- `SqlExecutorBase` subclasses `CapabilityExecutor`, and the framework
  bases subclass `ModelGateway`, so a type checker verifies port
  conformance for bases and leaves alike.
- The shared-base pattern is sanctioned for executors exactly as ADR
  0003 sanctions it for frameworks: shared mechanics live once in the
  base; leaves provide only the technology-specific hooks (connection,
  placeholder style, transient classification).
- Naming: bases are named `<Base>` (`SqlExecutorBase`,
  `StrandsGatewayBase`, `LangGraphGatewayBase`), not
  `<Technology><PortName>` — they are skeletons, not adapters. Leaves
  keep the `<Technology><PortName>` naming.

## Consequences

- A leaf that drops or mistypes a port method fails type checking
  instead of failing on first use.
- Adding a new executor kind means subclassing `SqlExecutorBase` (for
  DBAPI drivers) or `CapabilityExecutor` directly, and implementing only
  the hooks — no copy of the shared mechanics.
