"""SQL Server capability executor (<Technology><PortName>, §2).

Connection config (from DATA_CONNECTIONS[<name>]): server, database, and
either username/password or trusted_connection.

pyodbc uses ? placeholders, so params bind positionally in the order the
parameters are declared in the registry entry.
"""

from __future__ import annotations

from agent_api.adapters.sql_base import SqlExecutorBase
from agent_api.domain.capability import Capability
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.capability_executor import CapabilityExecutorError


class SqlServerCapabilityExecutor(SqlExecutorBase):
    """CapabilityExecutor running parameterized queries on SQL Server."""

    def __init__(self, connections: dict, retry_policy: RetryPolicy) -> None:
        super().__init__(connections, retry_policy)

    @property
    def kind(self) -> str:
        return "sqlserver"

    def _connect(self, connection: dict):
        # Local import: only this adapter needs pyodbc.
        try:
            import pyodbc
        except ImportError as exc:
            raise CapabilityExecutorError("pyodbc is not installed") from exc
        parts = [
            "DRIVER={ODBC Driver 18 for SQL Server}",
            f"SERVER={connection['server']}",
            f"DATABASE={connection.get('database', 'master')}",
        ]
        if connection.get("trusted_connection"):
            parts.append("Trusted_Connection=yes")
        else:
            parts += [f"UID={connection['username']}", f"PWD={connection['password']}"]
        parts.append("Encrypt=yes;TrustServerCertificate=no")
        return pyodbc.connect(";".join(parts), timeout=15)

    def _is_transient(self, exc: Exception) -> bool:
        try:
            import pyodbc
        except ImportError:
            return False
        # Connection/communication failures retry; bad SQL does not.
        return isinstance(exc, (pyodbc.OperationalError, pyodbc.InterfaceError))

    def _placeholders(self, capability: Capability, params: dict):
        # Build over ALL declared parameters, in declaration order, so a
        # missing optional never compresses later values onto earlier
        # placeholders (the old code did exactly that: a missing optional
        # mid-list silently misbound every later argument). A missing
        # required parameter fails closed here instead of reaching the
        # driver, and the placeholder count must match the argument count.
        ordered = []
        for p in capability.parameters:
            if p.name in params:
                ordered.append(params[p.name])
            elif p.required:
                raise CapabilityExecutorError(
                    f"missing required parameter: {p.name}"
                )
            else:
                ordered.append(None)
        query = capability.spec["query"]
        if query.count("?") != len(ordered):
            raise CapabilityExecutorError(
                f"capability '{capability.id}': query has {query.count('?')} "
                f"placeholders but {len(ordered)} parameters"
            )
        return query, ordered
