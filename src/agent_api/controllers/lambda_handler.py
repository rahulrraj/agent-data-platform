"""Inbound Lambda adapter (controller, §2).

Translates AWS Lambda invocation events into the existing use cases and
returns the standard Lambda proxy response dict. Knows nothing about model
providers — it only sees the FastAPI app built by
composition.build_app().

Placement: controllers/ holds inbound adapters, and a Lambda handler is
one: like controllers/http.py it translates a transport event into the
same AskAgentService use cases and maps errors to transport responses.
The only difference is the transport (a Lambda event dict instead of an
HTTP request), so the translation lives here, not in main.py and not in
the service layer.

Transport facts (read before deploying):

- Event shapes: Lambda Function URLs and API Gateway HTTP APIs both use
  payload version 2.0, and Mangum infers the shape from the event.
  Mangum also accepts API Gateway REST (v1) and ALB events, but those
  are not the documented path for this app.
- /api/chat is SSE, and Mangum buffers the whole ASGI response body
  (Mangum 0.22: "content will always be returned in a single response,
  never streamed"). On Lambda the chat reply therefore arrives as one
  complete SSE document — every data: line, then data: [DONE] — instead
  of incrementally. The body is still valid SSE and the UI parses it
  exactly the same way; only incremental display is lost. True
  incremental streaming needs a Function URL in RESPONSE_STREAM mode
  plus a streaming runtime path, which Mangum does not provide; that
  path is NOT implemented here and is a documented follow-up, not
  silently faked (the Python runtime interface client could not be
  verified from this sandbox's pip index).
- API Gateway CANNOT serve /api/chat: its integration timeout is 29
  seconds while an agent turn runs minutes, and it does not progressively
  stream responses. Serve chat through a Function URL (15-minute
  timeout) only. Health, ask, and ready fit either front door.
- State: a Lambda execution environment is ephemeral and may serve any
  invocation. CONVERSATION_STORE must be postgres: memory loses history
  on every invocation, and a sqlite file under /tmp is visible only to
  one execution environment, so sessions would not survive across
  invocations. EMBEDDING_CACHE_PATH must point at /tmp (the only
  writable directory); the cache then rebuilds on cold start, which at
  thousands of capabilities means thousands of embedding calls per cold
  start — a shared cache is the follow-up, not built here.
  These requirements are ENFORCED, not just documented (m-11):
  composition.build_app() fails fast when it runs under Lambda
  (AWS_LAMBDA_FUNCTION_NAME set) with a non-postgres store or a cache
  path outside /tmp.
- The app is built once per execution environment and reused across
  warm invocations (Lambda container reuse); cold starts pay the full
  build cost (model gateway construction, capability registry load).
"""

from __future__ import annotations

from typing import Any

from mangum import Mangum

from agent_api.composition import build_app

_mangum: Mangum | None = None


def _get_mangum() -> Mangum:
    """Build the ASGI app once per execution environment (Lambda reuse)."""
    global _mangum
    if _mangum is None:
        _mangum = Mangum(build_app(), lifespan="off")
    return _mangum


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entrypoint (see Dockerfile.lambda CMD).

    Delegates to Mangum, which translates the Lambda event into an ASGI
    scope for the FastAPI app and converts the response back into the
    standard proxy dict: statusCode, headers, body, isBase64Encoded.
    """
    return _get_mangum()(event, context)
