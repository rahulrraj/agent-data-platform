"""Adapter tests: HTTP capability executor with a stubbed transport."""

import httpx
import pytest

from agent_api.adapters.http_capability_executor import HttpCapabilityExecutor
from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.capability_executor import CapabilityExecutorError

CONNECTIONS = {
    "orders_api": {"base_url": "https://api.example.com", "api_key": "secret"},
}

ORDER_CAP = Capability(
    id="order_status",
    description="Order status by order number",
    kind="http",
    connection="orders_api",
    mode="read",
    parameters=(
        ParameterSpec(name="order_number", type="string", required=True),
    ),
    spec={
        "request": {
            "method": "GET",
            "path": "/orders/{order_number}",
            "fields": ["status", "tracking_number"],
        }
    },
)


class StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class StubClient:
    """Replaces httpx.Client: records the request, returns canned responses."""

    last_request = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None, params=None):
        StubClient.last_request = {"url": url, "headers": headers, "params": params}
        return StubResponse(
            200,
            {"status": "shipped", "tracking_number": "1Z999", "internal_notes": "x"},
        )

    def post(self, url, headers=None, json=None):
        StubClient.last_request = {"url": url, "headers": headers, "json": json}
        return StubResponse(200, {"ok": True})


@pytest.fixture()
def executor(monkeypatch):
    monkeypatch.setattr(httpx, "Client", StubClient)
    return HttpCapabilityExecutor(CONNECTIONS, RetryPolicy(max_attempts=1))


def test_get_substitutes_path_and_filters_fields(executor):
    result = executor.execute(ORDER_CAP, {"order_number": "ORD-1"})
    assert result == {"data": {"status": "shipped", "tracking_number": "1Z999"}}
    req = StubClient.last_request
    assert req["url"] == "https://api.example.com/orders/ORD-1"
    assert req["headers"]["Authorization"] == "Bearer secret"
    assert req["params"] == {}  # order_number was consumed by the path


def test_unknown_connection_is_a_clean_error(executor):
    cap = Capability(
        id="x", description="x", kind="http", connection="missing",
        mode="read", parameters=(), spec={"request": {"method": "GET", "path": "/"}},
    )
    with pytest.raises(CapabilityExecutorError, match="not configured"):
        executor.execute(cap, {})


def test_non_read_mode_refused(executor):
    cap = Capability(
        id="x", description="x", kind="http", connection="orders_api",
        mode="write", parameters=(), spec={"request": {"method": "POST", "path": "/"}},
    )
    with pytest.raises(CapabilityExecutorError, match="non-read"):
        executor.execute(cap, {})
