"""Snowflake capability executor (<Technology><PortName>, §2).

Connection config (from DATA_CONNECTIONS[<name>]): account, user, and either
private_key (PEM string) or password, plus warehouse/database/schema/role.

Queries use %(name)s placeholders; params bind by name — the model can never
alter the query text, only fill values.
"""

from __future__ import annotations

from agent_api.adapters.sql_base import SqlExecutorBase
from agent_api.domain.capability import Capability
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.capability_executor import CapabilityExecutorError


class SnowflakeCapabilityExecutor(SqlExecutorBase):
    """CapabilityExecutor running parameterized queries on Snowflake."""

    def __init__(self, connections: dict, retry_policy: RetryPolicy) -> None:
        super().__init__(connections, retry_policy)

    @property
    def kind(self) -> str:
        return "snowflake"

    def _connect(self, connection: dict):
        # Local import: only this adapter needs the snowflake driver.
        try:
            import snowflake.connector
        except ImportError as exc:
            raise CapabilityExecutorError(
                "snowflake-connector-python is not installed"
            ) from exc
        kwargs = {
            "account": connection["account"],
            "user": connection["user"],
            "warehouse": connection.get("warehouse"),
            "database": connection.get("database"),
            "schema": connection.get("schema"),
            "role": connection.get("role"),
        }
        if connection.get("private_key"):
            kwargs["private_key"] = connection["private_key"].encode()
        elif connection.get("password"):
            kwargs["password"] = connection["password"]
        else:
            raise CapabilityExecutorError(
                "snowflake connection needs 'private_key' or 'password'"
            )
        return snowflake.connector.connect(**{k: v for k, v in kwargs.items() if v})

    def _is_transient(self, exc: Exception) -> bool:
        try:
            from snowflake.connector import errors as sf_errors
        except ImportError:
            return False
        # Connection-level failures are worth retrying; bad SQL is not.
        return isinstance(
            exc,
            (
                sf_errors.InterfaceError,
                sf_errors.OperationalError,
                sf_errors.DatabaseError,
            ),
        ) and not isinstance(exc, sf_errors.ProgrammingError)

    def _placeholders(self, capability: Capability, params: dict):
        return capability.spec["query"], params
