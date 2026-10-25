"""Data capabilities: what organization data the agent may access.

A Capability is a named, parameterized way to reach one data source — never
raw SQL or a raw URL from the model. The model supplies parameter *values*
only; the query/path is fixed in the registry entry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


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
