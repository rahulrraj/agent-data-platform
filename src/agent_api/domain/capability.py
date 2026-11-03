"""Data capabilities: what organization data the agent may access.

A Capability is a named, parameterized way to reach one data source — never
raw SQL or a raw URL from the model. The model supplies parameter *values*
only; the query/path is fixed in the registry entry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# Result-trimming defaults for HTTP capabilities (see
# doc/data-capabilities.md): lists are capped at 50 by default, mirroring
# the SQL row cap; a capability may opt in to a higher cap, up to 500.
# The SQL cap is unchanged and unaffected by these settings.
DEFAULT_MAX_RESULTS = 50
MAX_MAX_RESULTS = 500
_KEEP_VALUES = frozenset({"first", "last"})


@dataclass(frozen=True)
class ParameterSpec:
    """One named parameter a capability accepts."""

    name: str
    type: str  # "string" | "integer" | "number" | "boolean"
    required: bool
    description: str = ""


@dataclass(frozen=True)
class Capability:
    """A single data-access capability from the registry."""

    id: str
    description: str
    kind: str  # "snowflake" | "sqlserver" | "http"
    connection: str  # named connection; credentials resolve from config
    mode: str  # "read" — executors refuse anything else
    parameters: tuple[ParameterSpec, ...] = ()
    spec: dict = field(default_factory=dict)  # kind-specific: query | request
    # Result trimming (HTTP executors only; the SQL row cap is separate
    # and unchanged). `list_paths` names dotted paths to nested lists
    # inside a dict response; when empty, the top-level list is trimmed
    # instead. `keep` selects which end of a list survives trimming.
    list_paths: tuple[str, ...] = ()
    keep: str = "first"  # "first" | "last"
    max_results: int = DEFAULT_MAX_RESULTS  # 1..MAX_MAX_RESULTS
    # Token forwarding (HTTP executors only; SQL kinds validate-but-ignore
    # this at load, like the trimming settings above). When true and a
    # user token is present in the per-turn request context, the HTTP
    # executor sends it as the downstream Authorization bearer token
    # (ADR 0008). The token never reaches the model: it travels via the
    # request context, never via tool arguments or history.
    forward_user_token: bool = False

    def __post_init__(self) -> None:
        # Domain invariant, enforced wherever a Capability is built;
        # the registry loader validates first with entry-named errors.
        # A list is accepted here and normalised to a tuple (the
        # registry loader passes a tuple; direct callers may pass a list).
        if isinstance(self.list_paths, list):
            object.__setattr__(self, "list_paths", tuple(self.list_paths))
        if isinstance(self.list_paths, str) or not isinstance(
            self.list_paths, tuple
        ):
            raise ValueError(
                f"capability {self.id!r}: 'list_paths' must be a list of "
                f"non-empty strings (got {self.list_paths!r})"
            )
        for path in self.list_paths:
            if not isinstance(path, str) or not path:
                raise ValueError(
                    f"capability {self.id!r}: 'list_paths' must be a list "
                    f"of non-empty strings (got {path!r})"
                )
        if self.keep not in _KEEP_VALUES:
            raise ValueError(
                f"capability {self.id!r}: 'keep' must be 'first' or 'last' "
                f"(got {self.keep!r})"
            )
        if isinstance(self.max_results, bool) or not isinstance(
            self.max_results, int
        ):
            raise ValueError(
                f"capability {self.id!r}: 'max_results' must be an integer "
                f"(got {self.max_results!r})"
            )
        if not 1 <= self.max_results <= MAX_MAX_RESULTS:
            raise ValueError(
                f"capability {self.id!r}: 'max_results' must be between 1 "
                f"and {MAX_MAX_RESULTS} (got {self.max_results})"
            )
        if not isinstance(self.forward_user_token, bool):
            raise ValueError(
                f"capability {self.id!r}: 'forward_user_token' must be a "
                f"boolean (got {self.forward_user_token!r})"
            )


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Pure math, no dependencies.

    Dimension mismatch is an error, not a silent zip-truncate (m-16):
    a cached vector from a different embedding-model version must never
    produce a quietly wrong ranking.
    """
    if len(a) != len(b):
        raise ValueError(
            f"cosine_similarity: dimension mismatch {len(a)} vs {len(b)}"
        )
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
