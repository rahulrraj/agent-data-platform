"""Shared mechanics for SQL-backed capability executors.

Subclasses provide the DBAPI connection; everything else — read-only
enforcement, parameterized execution, row caps, retries — lives here once.

Read-only enforcement is two layers: (1) this module's first-keyword
guard refuses any `mode: read` statement that does not start with
SELECT/WITH — a backstop, not a parser, and it must not be mistaken for
one; (2) the real enforcement is a read-only database role with no write
grants on the connection used (see doc/data-capabilities.md). A registry
entry is reviewed code, but defense in depth means a bad entry alone
cannot turn a read capability into a write.
Parameter styles differ per driver (documented per adapter):
- snowflake: %(name)s placeholders (pyformat)
- sqlserver: ? placeholders, positional in registry parameter order
"""

from __future__ import annotations

import re

from agent_api.domain.capability import Capability
from agent_api.platform.retry import RetryPolicy, call_with_retry
from agent_api.ports.capability_executor import (
    CapabilityExecutor,
    CapabilityExecutorError,
)


class SqlExecutorBase(CapabilityExecutor):
    """CapabilityExecutor skeleton for DBAPI databases.

    Subclassing the port (m-2) means signature drift in the base or a
    leaf is caught by a type checker, not only at runtime — the same
    shared-base sanction ADR 0003 gives the framework bases, now
    recorded for executors in ADR 0006.
    """

    max_rows = 50  # guardrail: never dump unbounded results into the model

    def __init__(
        self,
        connections: dict,
        retry_policy: RetryPolicy,
    ) -> None:
        self._connections = connections
        self._retry = retry_policy

    # -- subclass hooks -----------------------------------------------------

    @property
    def kind(self) -> str:
        raise NotImplementedError

    def _connect(self, connection: dict):
        """Open a DBAPI connection from the named connection's config."""
        raise NotImplementedError

    def _is_transient(self, exc: Exception) -> bool:
        return False

    def _placeholders(self, capability: Capability, params: dict):
        """Return (query, args) in the driver's parameter style."""
        raise NotImplementedError

    # -- shared mechanics ---------------------------------------------------

    def _connection_config(self, capability: Capability) -> dict:
        try:
            return self._connections[capability.connection]
        except KeyError:
            raise CapabilityExecutorError(
                f"connection '{capability.connection}' is not configured "
                "(DATA_CONNECTIONS)"
            ) from None

    @staticmethod
    def _first_keyword(query: str) -> str:
        """First SQL keyword, skipping whitespace and comments.

        Backstop only: it looks at the start of the statement, it does
        not parse SQL. Multi-statement strings and exotic syntax are
        still the read-only DB role's problem, not this guard's.
        """
        text = query.strip()
        while True:
            if text.startswith("/*"):
                end = text.find("*/")
                if end == -1:
                    return ""
                text = text[end + 2 :].strip()
            elif text.startswith("--"):
                text = text.split("\n", 1)[-1].strip() if "\n" in text else ""
            else:
                break
        text = text.lstrip("(").strip()
        match = re.match(r"[A-Za-z]+", text)
        return match.group(0).upper() if match else ""

    def execute(self, capability: Capability, params: dict) -> dict:
        if capability.mode != "read":
            raise CapabilityExecutorError(
                f"refusing non-read capability '{capability.id}' (mode={capability.mode})"
            )
        query = capability.spec.get("query")
        if not query:
            raise CapabilityExecutorError(
                f"capability '{capability.id}' has no query in its registry entry"
            )
        if self._first_keyword(query) not in ("SELECT", "WITH"):
            raise CapabilityExecutorError(
                f"refusing non-read statement for capability '{capability.id}' "
                f"(mode=read requires SELECT/WITH)"
            )
        try:
            return call_with_retry(
                lambda: self._execute_once(capability, params, query),
                policy=self._retry,
                is_transient=self._is_transient,
            )
        except CapabilityExecutorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CapabilityExecutorError(f"{self.kind} query failed: {exc}") from exc

    def _execute_once(
        self, capability: Capability, params: dict, query: str
    ) -> dict:
        query, args = self._placeholders(capability, params)
        config = self._connection_config(capability)
        conn = self._connect(config)
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(query, args)
                columns = [d[0] for d in (cursor.description or [])]
                rows = cursor.fetchmany(self.max_rows + 1)
            finally:
                cursor.close()
        finally:
            conn.close()
        truncated = len(rows) > self.max_rows
        return {
            "rows": [dict(zip(columns, row)) for row in rows[: self.max_rows]],
            "truncated": truncated,
        }
