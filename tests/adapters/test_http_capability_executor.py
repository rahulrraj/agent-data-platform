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


def _sequence_executor(monkeypatch, statuses, max_attempts=3, connections=None):
    client = _SequenceClient(statuses)

    class _ClientFactory:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return client

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "Client", _ClientFactory)
    executor = HttpCapabilityExecutor(
        CONNECTIONS if connections is None else connections,
        RetryPolicy(max_attempts=max_attempts, base_delay=0),
    )
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


# --- Token forwarding (ADR 0008) ---

from agent_api.adapters.http_capability_executor import _redact
from agent_api.platform.request_context import request_context

_NO_KEY_CONNECTIONS = {
    "orders_api": {"base_url": "https://api.example.com"},
}

FORWARD_CAP = Capability(
    id="order_status_forwarded",
    description="Order status by order number, forwarding the user token",
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
    forward_user_token=True,
)

FAKE_USER_TOKEN = "fake-user-token-1"


def _forwarding_executor(monkeypatch, connections=_NO_KEY_CONNECTIONS):
    monkeypatch.setattr(httpx, "Client", StubClient)
    return HttpCapabilityExecutor(connections, RetryPolicy(max_attempts=1))


def test_forwarding_sends_user_token_when_flag_and_token(monkeypatch):
    executor = _forwarding_executor(monkeypatch)
    with request_context("user-1", FAKE_USER_TOKEN):
        result = executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})
    assert result == {"data": {"status": "shipped", "tracking_number": "1Z999"}}
    assert StubClient.last_request["headers"]["Authorization"] == f"Bearer {FAKE_USER_TOKEN}"


def test_forwarding_absent_when_flag_off_even_with_token(monkeypatch):
    executor = _forwarding_executor(monkeypatch, CONNECTIONS)
    with request_context("user-1", FAKE_USER_TOKEN):
        executor.execute(ORDER_CAP, {"order_number": "ORD-1"})
    # Static service credential only; the user token never reaches a
    # capability that did not opt in.
    assert StubClient.last_request["headers"]["Authorization"] == "Bearer secret"


def test_forwarding_absent_when_no_token(monkeypatch):
    executor = _forwarding_executor(monkeypatch)
    executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})
    assert "Authorization" not in StubClient.last_request["headers"]
    with request_context("user-1", None):
        executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})
    assert "Authorization" not in StubClient.last_request["headers"]


def test_forwarding_preserves_connection_extra_headers(monkeypatch):
    connections = {
        "orders_api": {
            "base_url": "https://api.example.com",
            "headers": {"X-Custom": "1"},
        }
    }
    executor = _forwarding_executor(monkeypatch, connections)
    with request_context("user-1", FAKE_USER_TOKEN):
        executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})
    headers = StubClient.last_request["headers"]
    assert headers["Authorization"] == f"Bearer {FAKE_USER_TOKEN}"
    assert headers["X-Custom"] == "1"


def test_forwarding_conflict_with_static_key_raises_without_sending(monkeypatch):
    # Load-time check is the enforcement; this is the defensive
    # duplicate: both identities present -> neither is sent.
    executor, client = _sequence_executor(monkeypatch, [200])
    with request_context("user-1", FAKE_USER_TOKEN):
        with pytest.raises(CapabilityExecutorError, match="refusing to send either") as excinfo:
            executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})
    assert FAKE_USER_TOKEN not in str(excinfo.value)
    assert client.calls == 0


@pytest.mark.parametrize("header_name", ["Authorization", "authorization"])
def test_forwarding_conflict_with_static_header_raises(monkeypatch, header_name):
    connections = {
        "orders_api": {
            "base_url": "https://api.example.com",
            "headers": {header_name: "Bearer static-service-token"},
        }
    }
    executor = _forwarding_executor(monkeypatch, connections)
    with request_context("user-1", FAKE_USER_TOKEN):
        with pytest.raises(CapabilityExecutorError, match="refusing to send either"):
            executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})


@pytest.mark.parametrize("status", [401, 403])
def test_downstream_auth_failure_not_retried(monkeypatch, status):
    executor, client = _sequence_executor(
        monkeypatch, [status, 200], connections=_NO_KEY_CONNECTIONS
    )
    with request_context("user-1", FAKE_USER_TOKEN):
        with pytest.raises(CapabilityExecutorError, match=f"http {status}"):
            executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})
    assert client.calls == 1


def test_downstream_auth_failure_carries_no_token_or_body(monkeypatch, caplog):
    body = f"denied for Bearer {FAKE_USER_TOKEN}: not your order"
    executor = _raw_executor(
        monkeypatch, _RawResponse(401, body, {"error": "denied"}),
        connections=_NO_KEY_CONNECTIONS,
    )
    with request_context("user-1", FAKE_USER_TOKEN):
        with pytest.raises(CapabilityExecutorError) as excinfo:
            executor.execute(FORWARD_CAP, {"order_number": "ORD-1"})
    assert "http 401" in str(excinfo.value)
    assert FAKE_USER_TOKEN not in str(excinfo.value)
    assert "denied" not in str(excinfo.value)
    # The server-side log is redacted too: no raw token anywhere.
    assert FAKE_USER_TOKEN not in caplog.text


def test_redact_masks_bearer_and_key_value_fragments():
    assert "abc123" not in _redact("Authorization: Bearer abc123")
    assert "***" in _redact("Authorization: Bearer abc123")
    assert "xyz" not in _redact("api_key=xyz")
    assert "secretvalue9" not in _redact("token: secretvalue9")



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


def _raw_executor(monkeypatch, response, connections=None):
    client = _RawClient(response)

    class _ClientFactory:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return client

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(httpx, "Client", _ClientFactory)
    return HttpCapabilityExecutor(
        CONNECTIONS if connections is None else connections,
        RetryPolicy(max_attempts=1),
    )


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

# --- Per-capability result trimming: list_paths / keep / max_results ---


def _trim_cap(**overrides):
    kwargs = {
        "id": "prices",
        "description": "x",
        "kind": "http",
        "connection": "orders_api",
        "mode": "read",
        "parameters": (),
        "spec": {"request": {"method": "GET", "path": "/prices"}},
    }
    kwargs.update(overrides)
    return Capability(**kwargs)


def test_nested_path_trimmed_with_default_first_50(monkeypatch):
    payload = {
        "symbol": "ACME",
        "history": {"prices": list(range(60)), "other": [1, 2]},
    }
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    cap = _trim_cap(list_paths=["history.prices"])
    result = executor.execute(cap, {})
    assert result["truncated"] is True
    assert result["data"]["history"]["prices"] == list(range(50))
    # Untouched siblings survive, including a shorter nested list.
    assert result["data"]["symbol"] == "ACME"
    assert result["data"]["history"]["other"] == [1, 2]
    # Registry defaults: first 50, cap 50.
    assert (cap.list_paths, cap.keep, cap.max_results) == (
        ("history.prices",), "first", 50,
    )


def test_keep_last_keeps_most_recent_rows_nested(monkeypatch):
    payload = {"history": {"prices": list(range(60))}}
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(
        _trim_cap(list_paths=["history.prices"], keep="last"), {}
    )
    assert result["truncated"] is True
    assert result["data"]["history"]["prices"] == list(range(10, 60))


def test_keep_first_vs_last_on_top_level_list(monkeypatch):
    payload = list(range(60))
    first = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    assert first.execute(_trim_cap(keep="first"), {}) == {
        "data": list(range(50)), "truncated": True,
    }
    last = _raw_executor(monkeypatch, _RawResponse(200, "x", list(range(60))))
    assert last.execute(_trim_cap(keep="last"), {}) == {
        "data": list(range(10, 60)), "truncated": True,
    }


def test_default_behavior_unchanged_short_list_not_truncated(monkeypatch):
    payload = list(range(40))
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    cap = _trim_cap()
    assert (cap.list_paths, cap.keep, cap.max_results) == ((), "first", 50)
    assert executor.execute(cap, {}) == {"data": list(range(40)), "truncated": False}


def test_max_results_opt_in_up_to_500(monkeypatch):
    payload = {"history": {"prices": list(range(120))}}
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(
        _trim_cap(list_paths=["history.prices"], max_results=200), {}
    )
    assert result["truncated"] is False
    assert result["data"]["history"]["prices"] == list(range(120))

    payload = {"history": {"prices": list(range(600))}}
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(
        _trim_cap(list_paths=["history.prices"], max_results=500), {}
    )
    assert result["truncated"] is True
    assert result["data"]["history"]["prices"] == list(range(500))


def test_truncated_flag_when_nothing_trimmed_is_false_but_present(monkeypatch):
    payload = {"history": {"prices": list(range(30))}}
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(_trim_cap(list_paths=["history.prices"]), {})
    assert result == {"data": payload, "truncated": False}


def test_trimming_at_any_of_multiple_paths_sets_truncated(monkeypatch):
    payload = {
        "a": {"items": list(range(60))},
        "b": {"items": list(range(10))},
    }
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(
        _trim_cap(list_paths=["a.items", "b.items"], keep="last"), {}
    )
    assert result["truncated"] is True
    assert result["data"]["a"]["items"] == list(range(10, 60))
    assert result["data"]["b"]["items"] == list(range(10))


def test_unresolvable_paths_left_untouched(monkeypatch):
    payload = {
        "history": {"prices": list(range(60)), "note": "text"},
        "scalar": 7,
    }
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(
        _trim_cap(
            list_paths=[
                "missing.items",       # missing key
                "history.note.items",  # non-dict intermediate
                "scalar",              # non-list terminal
                "history.prices",      # resolves: must still trim
            ]
        ),
        {},
    )
    assert result["truncated"] is True
    assert result["data"]["history"]["prices"] == list(range(50))
    assert result["data"]["history"]["note"] == "text"
    assert result["data"]["scalar"] == 7

    # Only unresolvable paths: nothing trimmed, flag present and false.
    payload = {"history": {"prices": list(range(60))}}
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(_trim_cap(list_paths=["missing.items"]), {})
    assert result == {"data": payload, "truncated": False}


def test_top_level_list_still_capped_when_list_paths_set(monkeypatch):
    # list_paths only resolve inside dict payloads; a list root is
    # never left uncapped by a registry entry naming nested paths.
    payload = list(range(60))
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(_trim_cap(list_paths=["history.prices"]), {})
    assert result == {"data": list(range(50)), "truncated": True}


def test_fields_does_not_reach_nested_lists(monkeypatch):
    payload = {
        "history": {"prices": [{"close": i, "volume": i} for i in range(60)]},
        "extra": "dropped",
    }
    cap = _trim_cap(
        list_paths=["history.prices"],
        spec={"request": {"method": "GET", "path": "/prices",
                          "fields": ["history"]}},
    )
    executor = _raw_executor(monkeypatch, _RawResponse(200, "x", payload))
    result = executor.execute(cap, {})
    assert result["truncated"] is True
    assert result["data"] == {
        "history": {"prices": [{"close": i, "volume": i} for i in range(50)]}
    }
