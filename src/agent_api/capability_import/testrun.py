"""Run generated question files against the registry + a stub API.

Hermetic: no model, no network. Discovery runs through the real
``discover_capability`` tool against a deterministic keyword
embedding — a stand-in for a real embedding model. It pins the
ranking/threshold mechanics (which capability wins, near-ties,
no-match), not embedding quality. Execution runs through the real
``execute_capability`` tool against the real HTTP executor, whose
httpx client is patched onto a ``MockTransport`` serving example
bodies built from the spec's response schemas.

What this proves, and what it does not:

- Proves: the question set resolves to the right capability, the
  request is built as the registry declares it, ``fields`` trimming
  holds, and missing-param / 404 failures surface cleanly.
- Does not prove: value extraction (the ``params`` in the question
  file are the values a *correct* extraction would produce), real
  embedding quality, or anything about SQL-kind capabilities
  (their cases run discovery only; execution is reported skipped).

Stub semantics: a request whose path-parameter values are all known
(spec examples, plus the question file's successful cases) gets a
200 with an example body; any other path-parameter value gets a
404. Query parameters never 404 — they are filters. List responses
are served with three items so list shape is visible.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from unittest import mock

import httpx
import yaml

from agent_api.adapters.http_capability_executor import HttpCapabilityExecutor
from agent_api.adapters.yaml_capability_repository import YamlCapabilityRepository
from agent_api.capability_import.questions import example_for_param
from agent_api.capability_import.schemas import deref, example_value
from agent_api.platform.retry import RetryPolicy
from agent_api.tools import ToolDeps
from agent_api.tools.discover_capability.tool import (
    create_tool as create_discover,
)
from agent_api.tools.execute_capability.tool import (
    create_tool as create_execute,
)

_STUB_BASE_URL = "http://stub.local"


class KeywordEmbeddingGateway:
    """Deterministic test-only embeddings: binary token-presence vectors.

    Mirrors tests/tools/test_capability_discovery.py: cosine
    similarity measures distinctive-token overlap between a need and
    a capability description — a stable stand-in for a real embedding
    model. Not a quality claim about embeddings; it pins the mapping
    logic (ranking, threshold, single-best-match).
    """

    model_name = "keyword-test"
    similarity_threshold = 0.60

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


def _index_text(capability) -> str:
    """Text embedded for a capability: description + param descriptions.

    Mirrors the discover tool's indexing: phrasings that use
    parameter synonyms match on the parameter text too.
    """
    lines = [capability.description]
    for p in capability.parameters:
        if p.description:
            lines.append(f"{p.name}: {p.description}")
    return "\n".join(lines)


def _template_regex(path_template: str):
    """Compile a path template to a regex with named param groups."""
    parts = re.split(r"\{(\w+)\}", path_template)
    pattern = ""
    names = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            pattern += re.escape(part)
        else:
            names.append(part)
            pattern += f"(?P<{part}>[^/]+)"
    return re.compile("^" + pattern + "$"), names


class _StubRoute:
    def __init__(self, method, path_template, response_schema, known):
        self.method = method
        self.path_template = path_template
        self.regex, self.param_names = _template_regex(path_template)
        self.response_schema = response_schema
        self.known = known  # param name -> set of known values (as strings)


def _spec_param_examples(op, path_item, spec):
    """Known entity values per parameter, from the spec's examples."""
    known: dict = {}
    raw_params = []
    shared = path_item.get("parameters", []) or []
    if isinstance(shared, list):
        raw_params.extend(shared)
    own = op.get("parameters", []) or []
    if isinstance(own, list):
        raw_params.extend(own)
    for raw in raw_params:
        try:
            param = deref(raw, spec)
        except ValueError:
            continue
        if not isinstance(param, dict) or not param.get("name"):
            continue
        value, _ = example_for_param(param, spec)
        known.setdefault(param["name"], set()).add(str(value))
    return known


def _build_routes(spec: dict):
    """GET operations -> stub routes with example response bodies."""
    routes = []
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return routes
    for path, raw_item in paths.items():
        if not isinstance(path, str):
            continue
        try:
            item = deref(raw_item, spec)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method == "parameters" or not isinstance(op, dict):
                continue
            if not isinstance(method, str) or method.lower() != "get":
                continue
            try:
                op = deref(op, spec)
            except ValueError:
                continue
            if not isinstance(op, dict):
                continue
            schema = None
            try:
                responses = op.get("responses") or {}
                response = responses.get("200") or responses.get(200) or {}
                response = deref(response, spec) if isinstance(response, dict) else {}
                content = response.get("content") or {}
                if isinstance(content, dict):
                    for media in content.values():
                        if isinstance(media, dict) and "schema" in media:
                            schema = media["schema"]
                            break
            except ValueError:
                schema = None
            routes.append(_StubRoute(
                method.upper(), path, schema,
                _spec_param_examples(op, item, spec),
            ))
    return routes


def _stub_handler(routes, spec, requests):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for route in routes:
            if route.method != request.method:
                continue
            match = route.regex.match(path)
            if not match:
                continue
            values = {
                name: urllib.parse.unquote(value)
                for name, value in match.groupdict().items()
            }
            requests.append({
                "method": request.method,
                "path": path,
                "params": dict(request.url.params),
            })
            for name, value in values.items():
                known = route.known.get(name)
                if known is not None and value not in known:
                    return httpx.Response(
                        404, json={"error": "not found"}, request=request
                    )
            if route.response_schema is None:
                body = {"ok": True}
            else:
                body = example_value(route.response_schema, spec)
                if isinstance(body, list):
                    body = body * 3
            return httpx.Response(200, json=body, request=request)
        return httpx.Response(
            404, json={"error": "no such operation"}, request=request
        )

    return handler


def _load_questions(path: str):
    with open(path) as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"question file {path!r} is not a YAML mapping")
    cases = doc.get("cases") or []
    controls = doc.get("controls") or []
    if not isinstance(cases, list) or not isinstance(controls, list):
        raise ValueError(f"question file {path!r}: 'cases'/'controls' must be lists")
    normalized = []
    for raw in cases:
        if not isinstance(raw, dict):
            raise ValueError(f"question file {path!r}: a case is not a mapping: {raw!r}")
        for key in ("capability", "question", "expected"):
            if not raw.get(key):
                raise ValueError(
                    f"question file {path!r}: case missing {key!r}: {raw!r}"
                )
        if raw["expected"] not in ("match", "no_match"):
            raise ValueError(
                f"question file {path!r}: bad expected {raw['expected']!r}"
            )
        expect_result = raw.get("expect_result", "ok")
        if raw.get("execute") and expect_result not in (
            "ok", "missing_param", "not_found"
        ):
            raise ValueError(
                f"question file {path!r}: bad expect_result {expect_result!r}"
            )
        normalized.append(raw)
    for raw in controls:
        if not isinstance(raw, dict) or not raw.get("question"):
            raise ValueError(f"question file {path!r}: a control is malformed: {raw!r}")
        normalized.append({
            "capability": None,
            "type": "control",
            "question": raw["question"],
            "params": {},
            "expected": "no_match",
            "execute": False,
        })
    return normalized


def _parse_discovery(raw: str):
    """(matched_id, candidate_ids, is_no_match) for a discover result."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, [], True
    if not isinstance(payload, dict):
        return None, [], True
    if payload.get("ambiguous"):
        candidates = payload.get("candidates") or []
        ids = [c.get("id") for c in candidates if isinstance(c, dict)]
        return None, ids, False
    if payload.get("id"):
        return payload["id"], [payload["id"]], False
    return None, [], True


def _expected_path(entry, params: dict) -> str:
    """The registry path template with values substituted + encoded."""
    template = (entry.spec.get("request") or {}).get("path", "")

    def _sub(match):
        name = match.group(1)
        return urllib.parse.quote(str(params.get(name, "")), safe="")

    return re.sub(r"\{(\w+)\}", _sub, template)


def run_question_file(questions_path: str, registry_path: str, spec: dict):
    """Run a question file. Returns a report dict.

    Raises ValueError for a malformed question file and propagates
    registry load failures (``CapabilityRepositoryError``) — both
    are caller errors, not case failures.
    """
    cases = _load_questions(questions_path)
    repository = YamlCapabilityRepository(registry_path)
    capabilities = repository.list_all()
    routes = _build_routes(spec if isinstance(spec, dict) else {})
    # The question file's successful cases name the entities the stub
    # must answer 200 for — including hand-edited values the spec
    # never saw.
    for case in cases:
        if not case.get("execute") or case.get("expect_result") != "ok":
            continue
        entry = repository.get(case["capability"])
        if entry is None:
            continue
        template = (entry.spec.get("request") or {}).get("path", "")
        for route in routes:
            if route.path_template != template:
                continue
            for name in route.param_names:
                if name in (case.get("params") or {}):
                    route.known.setdefault(name, set()).add(
                        str(case["params"][name])
                    )
    texts = [_index_text(cap) for cap in capabilities]
    texts.extend(case["question"] for case in cases)
    gateway = KeywordEmbeddingGateway(texts)
    connections = {
        cap.connection: {
            "base_url": _STUB_BASE_URL,
            "allow_private_base_url": True,
        }
        for cap in capabilities
    }
    executor = HttpCapabilityExecutor(connections, RetryPolicy(max_attempts=1))
    deps = ToolDeps(
        capability_registry=repository,
        embedding_gateway=gateway,
        capability_executors={"http": executor},
        discovered_capability_ids=set(),
    )
    discover = create_discover(deps)
    execute = create_execute(deps)
    requests: list = []
    transport = httpx.MockTransport(_stub_handler(routes, spec, requests))
    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        return real_client(transport=transport, trust_env=False, timeout=30)

    results = []
    with mock.patch.object(httpx, "Client", _client_factory):
        for case in cases:
            result = _run_case(
                case, repository, discover, execute, requests
            )
            results.append(result)
    passed = sum(1 for r in results if r["passed"])
    return {
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "cases": results,
    }


def _run_case(case, repository, discover, execute, requests):
    result = {
        "capability": case.get("capability"),
        "type": case.get("type", "control"),
        "question": case["question"],
        "passed": True,
        "failures": [],
        "notes": [],
    }
    expected = case["expected"]
    raw = discover(case["question"])
    matched_id, candidate_ids, is_no_match = _parse_discovery(raw)
    if expected == "no_match":
        if not is_no_match:
            result["passed"] = False
            result["failures"].append(
                f"expected no match, discovery returned {candidate_ids}"
            )
        return result
    # expected == "match"
    capability_id = case["capability"]
    if is_no_match:
        result["passed"] = False
        result["failures"].append("discovery returned no match")
        return result
    if matched_id is not None and matched_id != capability_id:
        result["passed"] = False
        result["failures"].append(
            f"discovery returned {matched_id!r}, expected {capability_id!r}"
        )
        return result
    if matched_id is None and capability_id not in candidate_ids:
        result["passed"] = False
        result["failures"].append(
            f"discovery returned ambiguous {candidate_ids}, "
            f"expected {capability_id!r} among them"
        )
        return result
    if matched_id is None:
        result["notes"].append(
            f"ambiguous near-tie; {capability_id!r} among {candidate_ids}"
        )
    if not case.get("execute"):
        return result
    entry = repository.get(capability_id)
    if entry is None:
        result["passed"] = False
        result["failures"].append(f"{capability_id!r} is not in the registry")
        return result
    if entry.kind != "http":
        result["notes"].append(
            f"kind {entry.kind!r} is not stubbed; execution skipped"
        )
        return result
    params = case.get("params") or {}
    before = len(requests)
    outcome = execute(capability_id, params)
    expect_result = case.get("expect_result", "ok")
    if expect_result == "missing_param":
        missing = case.get("missing")
        want = f"Invalid parameters: missing required parameter: {missing}"
        if not outcome.startswith(want):
            result["passed"] = False
            result["failures"].append(
                f"expected {want!r}, got {outcome[:120]!r}"
            )
        return result
    if expect_result == "not_found":
        if "http 404" not in outcome:
            result["passed"] = False
            result["failures"].append(
                f"expected an http 404 mapping, got {outcome[:120]!r}"
            )
        return result
    # expect_result == "ok": a JSON result, fields-trimmed, right URL.
    if len(requests) != before + 1:
        result["passed"] = False
        result["failures"].append(
            f"expected exactly one stub request, saw {len(requests) - before}"
        )
        return result
    last = requests[-1]
    want_path = _expected_path(entry, params)
    if last["path"] != want_path:
        result["passed"] = False
        result["failures"].append(
            f"stub saw path {last['path']!r}, expected {want_path!r}"
        )
    try:
        payload = json.loads(outcome)
    except (json.JSONDecodeError, TypeError):
        result["passed"] = False
        result["failures"].append(f"execution did not return JSON: {outcome[:120]!r}")
        return result
    if not isinstance(payload, dict):
        result["passed"] = False
        result["failures"].append(f"execution returned {type(payload).__name__}, not an object")
        return result
    fields = (entry.spec.get("request") or {}).get("fields")
    data = payload.get("data")
    if fields and isinstance(data, dict):
        extra = sorted(set(data) - set(fields))
        if extra:
            result["passed"] = False
            result["failures"].append(
                f"response keys {extra} are outside fields {fields}"
            )
    if fields and isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                extra = sorted(set(item) - set(fields))
                if extra:
                    result["passed"] = False
                    result["failures"].append(
                        f"list item keys {extra} are outside fields {fields}"
                    )
                    break
    return result
