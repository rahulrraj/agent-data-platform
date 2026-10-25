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


class _SequenceClient:
    """httpx.Client stub returning a canned status sequence, counting calls."""

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.calls = 0
        self.last_url = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None, params=None):
        self.calls += 1
        self.last_url = url
        status = self._statuses.pop(0) if self._statuses else 200
        return StubResponse(status, {"status": "shipped"})

    def post(self, url, headers=None, json=None):
        self.calls += 1
        return StubResponse(200, {"ok": True})


def _sequence_executor(monkeypatch, statuses, max_attempts=3):
    client = _SequenceClient(statuses)

    class _ClientFactory:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return client

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "Client", _ClientFactory)
    executor = HttpCapabilityExecutor(CONNECTIONS, RetryPolicy(max_attempts=max_attempts, base_delay=0))
    return executor, client


def test_transient_500_retried_to_success(monkeypatch):
    # C-3: 500, 500, 200 with max_attempts=3 -> success, 3 upstream calls.
    executor, client = _sequence_executor(monkeypatch, [500, 500, 200])
    result = executor.execute(ORDER_CAP, {"order_number": "ORD-1"})
    # fields allowlist keeps both declared fields (missing -> None).
    assert result == {"data": {"status": "shipped", "tracking_number": None}}
    assert client.calls == 3


def test_transient_429_retried(monkeypatch):
    executor, client = _sequence_executor(monkeypatch, [429, 200])
    executor.execute(ORDER_CAP, {"order_number": "ORD-1"})
    assert client.calls == 2


def test_non_transient_400_not_retried(monkeypatch):
    executor, client = _sequence_executor(monkeypatch, [400, 200])
    with pytest.raises(CapabilityExecutorError, match="http 400"):
        executor.execute(ORDER_CAP, {"order_number": "ORD-1"})
    assert client.calls == 1


def test_read_mode_post_refused(monkeypatch):
    # M-8: mode=read + POST never reaches the upstream.
    executor, client = _sequence_executor(monkeypatch, [200])
    cap = Capability(
        id="x", description="x", kind="http", connection="orders_api",
        mode="read", parameters=(), spec={"request": {"method": "POST", "path": "/"}},
    )
    with pytest.raises(CapabilityExecutorError, match="GET-only"):
        executor.execute(cap, {})
    assert client.calls == 0


def test_path_value_percent_encoded(monkeypatch):
    # M-9: "?" and "=" in a path value cannot smuggle a query string.
    executor, client = _sequence_executor(monkeypatch, [200])
    executor.execute(ORDER_CAP, {"order_number": "x?admin=true"})
    assert client.last_url == "https://api.example.com/orders/x%3Fadmin%3Dtrue"


def test_path_value_with_slash_refused(monkeypatch):
    # M-9: traversal-style values are refused, not encoded-and-sent.
    executor, client = _sequence_executor(monkeypatch, [200])
    with pytest.raises(CapabilityExecutorError, match="must not contain '/'"):
        executor.execute(ORDER_CAP, {"order_number": "../admin"})
    assert client.calls == 0


def test_path_value_with_slash_allowed_when_opted_in(monkeypatch):
    executor, client = _sequence_executor(monkeypatch, [200])
    cap = Capability(
        id="nested", description="x", kind="http", connection="orders_api",
        mode="read",
        parameters=(ParameterSpec(name="order_number", type="string", required=True),),
        spec={"request": {"method": "GET", "path": "/orders/{order_number}",
                         "multi_segment_params": ["order_number"]}},
    )
    executor.execute(cap, {"order_number": "a/b"})
    assert client.last_url == "https://api.example.com/orders/a/b"


# --- m-4 / m-8 / m-14 / response-shape nits (2026-10-25) ---


def test_private_base_url_refused_at_construction():
    with pytest.raises(CapabilityExecutorError, match="not a public address"):
        HttpCapabilityExecutor(
            {"orders_api": {"base_url": "https://127.0.0.1:9000"}},
            RetryPolicy(max_attempts=1),
        )
    with pytest.raises(CapabilityExecutorError, match="must use https"):
        HttpCapabilityExecutor(
            {"orders_api": {"base_url": "http://api.example.com"}},
            RetryPolicy(max_attempts=1),
        )
    # Explicit dev opt-in passes validation.
    HttpCapabilityExecutor(
        {"orders_api": {"base_url": "http://127.0.0.1:9000",
                        "allow_private_base_url": True}},
        RetryPolicy(max_attempts=1),
    )


def test_upstream_error_body_not_returned_to_model(monkeypatch):
    executor, client = _sequence_executor(monkeypatch, [500], max_attempts=1)
    # _SequenceClient returns a dict payload; its str() is the "body".
    with pytest.raises(CapabilityExecutorError) as excinfo:
        executor.execute(ORDER_CAP, {"order_number": "ORD-1"})
    assert "shipped" not in str(excinfo.value)
    assert "http 500" in str(excinfo.value)


class _RawClient:
    """httpx.Client stub returning one canned raw response."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None, params=None):
        return self._response

    def post(self, url, headers=None, json=None):
        return self._response


class _RawResponse:
    def __init__(self, status_code, text, payload=None, json_raises=False):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not JSON")
        return self._payload


def _raw_executor(monkeypatch, response):
    client = _RawClient(response)

    class _ClientFactory:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return client

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "Client", _ClientFactory)
    return HttpCapabilityExecutor(CONNECTIONS, RetryPolicy(max_attempts=1))


def test_empty_204_returns_none_not_crash(monkeypatch):
    executor = _raw_executor(monkeypatch, _RawResponse(204, ""))
    assert executor.execute(ORDER_CAP, {"order_number": "ORD-1"}) == {"data": None}


def test_non_json_200_returned_as_text(monkeypatch):
    executor = _raw_executor(
        monkeypatch, _RawResponse(200, "plain text", json_raises=True)
    )
    assert executor.execute(ORDER_CAP, {"order_number": "ORD-1"}) == {"data": "plain text"}


def test_list_result_capped_and_fields_trim_items(monkeypatch):
    cap = Capability(
        id="orders", description="x", kind="http", connection="orders_api",
        mode="read", parameters=(),
        spec={"request": {"method": "GET", "path": "/orders",
                          "fields": ["status"]}},
    )
    payload = [{"status": f"s{i}", "secret_note": "x"} for i in range(60)]
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(cap, {})
    assert result["truncated"] is True
    assert len(result["data"]) == 50
    assert result["data"][0] == {"status": "s0"}


def test_malformed_path_template_not_missing_parameter(monkeypatch):
    executor, client = _sequence_executor(monkeypatch, [200])
    cap = Capability(
        id="x", description="x", kind="http", connection="orders_api",
        mode="read", parameters=(),
        spec={"request": {"method": "GET", "path": "/orders/{order_number"}},
    )
    with pytest.raises(CapabilityExecutorError, match="invalid path template"):
        executor.execute(cap, {})
    assert client.calls == 0
