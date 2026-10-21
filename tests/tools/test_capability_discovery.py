"""Functional tests for capability discovery: prompt -> API mapping.

Deterministic end-to-end check of the discover_capability tool: a fixture
registry is loaded through the real YamlCapabilityRepository, needs are
embedded with a deterministic keyword embedding (a stable stand-in for a
real embedding model), and each prompt must resolve to exactly the API
needed to answer it. If a description drifts so the wrong API wins, this
goes red — which is the point.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml

from agent_api.adapters.yaml_capability_repository import YamlCapabilityRepository
from agent_api.tools import ToolDeps
from agent_api.tools.discover_capability.tool import create_tool as create_discover


_FIXTURE_YAML = """
capabilities:
  - id: customer_by_id
    description: "Look up a customer's name, tier, and region by customer ID"
    kind: snowflake
    connection: snowflake_analytics
    mode: read
    parameters:
      customer_id: {type: string, required: true, description: "Customer ID, e.g. C-123"}
    query: "SELECT name, tier, region FROM dim_customers WHERE customer_id = %(customer_id)s"
  - id: accounts_by_customer
    description: "List all accounts belonging to a customer"
    kind: snowflake
    connection: snowflake_analytics
    mode: read
    parameters:
      customer_id: {type: string, required: true, description: "Customer ID, e.g. C-123"}
    query: "SELECT account_id, account_name FROM dim_accounts WHERE customer_id = %(customer_id)s"
  - id: returns_by_customer
    description: "Get investment returns for all accounts of a customer"
    kind: snowflake
    connection: snowflake_analytics
    mode: read
    parameters:
      customer_id: {type: string, required: true, description: "Customer ID, e.g. C-123"}
    query: "SELECT account_id, return_pct FROM fact_returns WHERE customer_id = %(customer_id)s"
  - id: orders_by_customer
    description: "List orders placed by a customer"
    kind: sqlserver
    connection: sqlserver_sales
    mode: read
    parameters:
      customer_id: {type: string, required: true, description: "Customer ID, e.g. C-123"}
    query: "SELECT order_id, total FROM orders WHERE customer_id = ?"
  - id: order_status
    description: "Look up where an order is — current status and tracking by order number"
    kind: http
    connection: orders_api
    mode: read
    parameters:
      order_number: {type: string, required: true, description: "Order number, e.g. O-789"}
    request: {method: GET, path: "/orders/{order_number}"}
"""

# prompt -> the one capability id that answers it
_PROMPT_TO_API = [
    ("What tier is customer C-123?", "customer_by_id"),
    ("Show me all accounts belonging to customer C-123", "accounts_by_customer"),
    # the money case: must beat accounts_by_customer, not just rank near it
    ("What are the returns for all accounts of customer C-123?", "returns_by_customer"),
    ("List orders placed by customer C-123", "orders_by_customer"),
    ("Where is my order O-789?", "order_status"),
]


class KeywordEmbeddingGateway:
    """Deterministic test-only embeddings: binary token-presence vectors.

    Cosine similarity then measures distinctive-token overlap between a need
    and a capability description — a stable stand-in for a real embedding
    model. Not a quality claim about embeddings; it pins the mapping logic
    (ranking, threshold, single-best-match).
    """

    _STOPWORDS = frozenset(
        "a an the is are was were what which how do does did me my "
        "for of to by in on all get list show look up".split()
    )

    def __init__(self, texts):
        vocab = set()
        for text in texts:
            vocab.update(self._tokens(text))
        self._vocab = sorted(vocab)

    @classmethod
    def _tokens(cls, text):
        out = []
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            if len(tok) < 2 or tok in cls._STOPWORDS:
                continue
            if tok.endswith("s") and not tok.endswith(("ss", "us")) and len(tok) > 3:
                tok = tok[:-1]  # naive singularization: returns->return
            out.append(tok)
        return out

    def embed(self, texts):
        vectors = []
        for text in texts:
            present = set(self._tokens(text))
            vectors.append([1.0 if t in present else 0.0 for t in self._vocab])
        return vectors

    def ping(self):
        return None


@pytest.fixture()
def discover(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(_FIXTURE_YAML)
    data = yaml.safe_load(_FIXTURE_YAML)
    descriptions = [c["description"] for c in data["capabilities"]]
    prompts = [prompt for prompt, _ in _PROMPT_TO_API]
    deps = ToolDeps(
        capability_registry=YamlCapabilityRepository(str(path)),
        embedding_gateway=KeywordEmbeddingGateway(descriptions + prompts),
        capability_executors={},
    )
    return create_discover(deps)


@pytest.mark.parametrize("prompt,expected_id", _PROMPT_TO_API)
def test_prompt_resolves_to_the_api_needed(discover, prompt, expected_id):
    match = json.loads(discover(prompt))
    assert match["id"] == expected_id, f"prompt {prompt!r} -> {match['id']!r}"


def test_match_carries_a_complete_parameter_schema(discover):
    match = json.loads(discover("What are the returns for all accounts of customer C-123?"))
    params = match["parameters"]["customer_id"]
    assert params["type"] == "string"
    assert params["required"] is True
    assert "C-123" in params["description"]  # example value for the model


def test_unrelated_prompt_reports_no_match(discover):
    result = discover("What's the weather in Boston?")
    assert "No data capability matches" in result
    # not JSON — the model must not try to parse it as a capability
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_only_one_api_returned(discover):
    # "only the api which is needed" — never a grab-bag
    match = json.loads(discover("What are the returns for all accounts of customer C-123?"))
    assert set(match.keys()) == {"id", "description", "parameters"}
