"""Composition root (§2, §7): construct, inject, wire, start.

This is the only place that reads Settings and the only place that picks
concrete adapters. No business logic lives here. `main.py` is a thin CLI
entrypoint over `build_app()`; `controllers/lambda_handler.py` imports
`build_app` from here — neither imports the other, so there is no
controllers <-> composition cycle (previously the Lambda handler imported
`main`, which imported `controllers.http`).

Gateway classes are imported lazily inside `_build_gateway_for()`, after
the proxy strip below: the `ollama` package builds an HTTP client at import
time, and importing it before the strip would crash on broken proxy env
vars (see ADR 0005). Nothing else in this module needs lazy imports.

Per-request model selection (ADR 0007) lives here too: `ModelGatewayFactory`
lazily builds and caches one gateway per allowlist key, so the service
resolves the gateway per call without ever knowing the matrix.
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI

from agent_api.adapters.bedrock_embedding_gateway import BedrockEmbeddingGateway
from agent_api.adapters.gemini_embedding_gateway import GeminiEmbeddingGateway
from agent_api.adapters.http_capability_executor import HttpCapabilityExecutor
from agent_api.adapters.in_memory_conversation_repository import InMemoryConversationRepository
from agent_api.adapters.oidc_token_validator import OidcTokenValidator
from agent_api.adapters.ollama_embedding_gateway import OllamaEmbeddingGateway
from agent_api.adapters.postgres_conversation_repository import (
    PostgresConversationRepository,
)
from agent_api.adapters.snowflake_capability_executor import SnowflakeCapabilityExecutor
from agent_api.adapters.sqlite_conversation_repository import SqliteConversationRepository
from agent_api.adapters.sqlite_embedding_cache import SqliteEmbeddingCache
from agent_api.adapters.sqlserver_capability_executor import SqlServerCapabilityExecutor
from agent_api.adapters.yaml_capability_repository import (
    YamlCapabilityRepository,
    validate_forwarding_conflicts,
)
from agent_api.config.settings import Settings
from agent_api.controllers.http import create_app
from agent_api.domain.prompts import build_system_prompt
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.conversation_repository import ConversationRepository
from agent_api.ports.model_gateway import ModelGateway, ModelGatewayError
from agent_api.ports.token_validator import TokenValidator
from agent_api.services.ask_agent import AskAgentService
from agent_api.tools import ToolDeps, load_tools, tool_names


def _strip_proxy_env() -> None:
    """Remove proxy env vars before any Ollama-backed import (ADR 0005).

    The sandbox's proxy values break the `ollama` package's import-time
    HTTP client, and localhost model calls need no proxy. This is the
    single sanctioned place for process-environment handling outside
    settings.py: adapters never touch os.environ.
    """
    for key in [k for k in os.environ if "proxy" in k.lower()]:
        del os.environ[key]


def model_label(settings: Settings) -> str:
    """The display label for the configured model (m-1).

    Canonical home for label construction: Settings.describe_model()
    stays as a thin lookup for callers that already hold a Settings
    object, but the composed app takes its label from here, next to
    the adapter selection it describes.
    """
    return settings.describe_model()


def _validate_lambda_settings(settings: Settings) -> None:
    """Fail fast on Lambda when the documented requirements are unmet (m-11).

    A memory store loses history on every invocation and a sqlite file
    under /tmp is visible to only one execution environment, so neither
    is a silent misconfiguration on Lambda — it is a startup error.
    Likewise the embedding cache must live under /tmp, the only
    writable directory. Outside Lambda (no AWS_LAMBDA_FUNCTION_NAME)
    this is a no-op: local/dev runs may use any store.
    """
    if not settings.lambda_runtime:
        return
    if settings.conversation_store != "postgres":
        raise RuntimeError(
            "On Lambda, CONVERSATION_STORE must be 'postgres' "
            f"(got {settings.conversation_store!r}): memory loses history "
            "every invocation and sqlite is per-execution-environment."
        )
    cache = settings.embedding_cache_path
    if not (cache == "/tmp" or cache.startswith("/tmp/")):
        raise RuntimeError(
            "On Lambda, EMBEDDING_CACHE_PATH must point under /tmp "
            f"(got {cache!r}): /tmp is the only writable directory."
        )


def _embedding_gateway(settings: Settings, retry_policy: RetryPolicy):
    """Single provider-resolution point for embeddings (m-1).

    Previously build_tool_deps() duplicated the provider branch that
    build_gateway() also makes; a future provider now has exactly one
    embedding branch to add here (the framework × provider model matrix
    stays in build_gateway, which is a different decision).
    """
    if settings.provider == "gemini":
        return GeminiEmbeddingGateway(
            settings.gemini_api_key, settings.gemini_embed_model,
            retry_policy=retry_policy,
        )
    if settings.provider == "bedrock":
        return BedrockEmbeddingGateway(
            settings.aws_region, settings.bedrock_embed_model,
            retry_policy=retry_policy,
        )
    return OllamaEmbeddingGateway(
        settings.ollama_host, settings.ollama_embed_model,
        retry_policy=retry_policy,
    )


def _gateway_class(framework: str, provider: str):
    """The framework × provider matrix (ADR 0003). Only the composition
    root knows it; the service just sees a ModelGateway.

    The imports are lazy and follow the proxy strip (see module
    docstring); calling this at startup for validation is safe because
    the strip runs first and is idempotent.
    """
    if framework == "langgraph" and provider == "bedrock":
        raise RuntimeError(
            "LangGraph + Bedrock is not built yet (Strands only for bedrock)."
        )
    _strip_proxy_env()
    # Lazy imports: must follow the strip (see module docstring).
    from agent_api.adapters.bedrock_model_gateway import BedrockModelGateway
    from agent_api.adapters.gemini_model_gateway import GeminiModelGateway
    from agent_api.adapters.langgraph_gemini_gateway import LangGraphGeminiGateway
    from agent_api.adapters.langgraph_ollama_gateway import LangGraphOllamaGateway
    from agent_api.adapters.ollama_model_gateway import OllamaModelGateway

    return {
        ("strands", "ollama"): OllamaModelGateway,
        ("strands", "gemini"): GeminiModelGateway,
        ("strands", "bedrock"): BedrockModelGateway,
        ("langgraph", "ollama"): LangGraphOllamaGateway,
        ("langgraph", "gemini"): LangGraphGeminiGateway,
    }[(framework, provider)]


def _build_gateway_for(
    framework: str,
    provider: str,
    model_id: str,
    settings: Settings,
    retry_policy: RetryPolicy,
    tools: list,
    system_prompt: str,
) -> ModelGateway:
    """Construct one gateway for a (framework, provider, model) triple.

    Construction is cheap and side-effect-free (adapters build their
    model object per turn); missing credentials are NOT validated here
    — the first call surfaces them as ModelGatewayError (a per-request
    502), so a listed provider without credentials never fails startup.
    Construction failures (e.g. a missing provider SDK) are translated
    to ModelGatewayError for the same reason.
    """
    gateway_class = _gateway_class(framework, provider)
    if provider == "gemini":
        kwargs = {"api_key": settings.gemini_api_key, "model_id": model_id}
    elif provider == "bedrock":
        kwargs = {"region_name": settings.aws_region, "model_id": model_id}
    else:
        kwargs = {"host": settings.ollama_host, "model_id": model_id}
    try:
        return gateway_class(
            tools=tools,
            system_prompt=system_prompt,
            retry_policy=retry_policy,
            **kwargs,
        )
    except ModelGatewayError:
        raise
    except Exception as exc:  # noqa: BLE001 — construction failure is a provider failure
        raise ModelGatewayError(
            f"{provider} gateway could not be built: {exc}"
        ) from exc


class ModelGatewayFactory:
    """Lazily builds and caches one gateway per allowlist key (ADR 0007).

    The framework stays as configured; only the provider + model vary
    per key. Framework × provider incompatibilities (langgraph ×
    bedrock) are rejected here, at startup, for every key — never at
    request time. Gateways build a fresh agent per turn internally, so
    sharing one cached gateway object per key across requests mirrors
    the previous single-shared-gateway behavior.
    """

    def __init__(
        self,
        settings: Settings,
        retry_policy: RetryPolicy,
        tools: list,
        system_prompt: str,
    ) -> None:
        self._settings = settings
        self._retry_policy = retry_policy
        self._tools = tools
        self._system_prompt = system_prompt
        self._cache: dict[str, ModelGateway] = {}
        # Startup validation: every resolvable (framework, provider)
        # pair must exist in the matrix before any request arrives.
        _gateway_class(settings.framework, settings.provider)
        for key, choice in settings.allowed_models.items():
            try:
                _gateway_class(settings.framework, choice.provider)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"ALLOWED_MODELS[{key!r}]: {exc}"
                ) from exc

    @property
    def available_keys(self) -> tuple[str, ...]:
        """Caller-facing keys, with "default" first."""
        return ("default",) + tuple(self._settings.allowed_models)

    def _default_model(self) -> str:
        return {
            "ollama": self._settings.ollama_model,
            "gemini": self._settings.gemini_model,
            "bedrock": self._settings.bedrock_model,
        }[self._settings.provider]

    def gateway_for(self, key: str | None) -> ModelGateway:
        """Return the cached gateway for `key`; build it on first use.

        None or "default" resolves to the configured provider + model.
        Unknown keys raise KeyError (the service turns that into a 400
        listing the available keys).
        """
        if key is None or key == "default":
            cache_key, provider, model = (
                "default",
                self._settings.provider,
                self._default_model(),
            )
        else:
            choice = self._settings.allowed_models.get(key)
            if choice is None:
                raise KeyError(key)
            cache_key, provider, model = key, choice.provider, choice.model
        if cache_key not in self._cache:
            self._cache[cache_key] = _build_gateway_for(
                self._settings.framework,
                provider,
                model,
                self._settings,
                self._retry_policy,
                self._tools,
                self._system_prompt,
            )
        return self._cache[cache_key]


def build_gateway(
    settings: Settings,
    retry_policy: RetryPolicy,
    tools: list,
    system_prompt: str,
) -> ModelGateway:
    """Build the configured (default) gateway immediately.

    Backward-compatible entry point for the single-model wiring; the
    composed app uses ModelGatewayFactory instead. Missing credentials
    are no longer a startup error here either — the first call surfaces
    them as ModelGatewayError.
    """
    return ModelGatewayFactory(
        settings, retry_policy, tools, system_prompt
    ).gateway_for("default")


def build_repository(settings: Settings) -> ConversationRepository:
    if settings.conversation_store == "sqlite":
        return SqliteConversationRepository(settings.sqlite_path)
    if settings.conversation_store == "postgres":
        return PostgresConversationRepository(settings.postgres_dsn)
    return InMemoryConversationRepository()


def _build_token_validator(settings: Settings) -> TokenValidator | None:
    """The OIDC token validator, or None when auth is disabled (ADR 0008).

    Auth disabled is the explicit dev opt-out only (AUTH_DISABLED=true);
    Settings.from_env() is fail-closed, so a non-disabled settings
    object always carries an issuer and audience here. The validator
    is constructed lazily — no JWKS fetch happens at startup; the
    first validated request fetches (and caches) the keys.
    """
    if settings.auth_disabled:
        return None
    return OidcTokenValidator(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        jwks_url=settings.auth_jwks_url or None,
    )


def build_tool_deps(settings: Settings, retry_policy: RetryPolicy) -> ToolDeps:
    """Wire the data-capability stack: registry + embeddings + executors."""
    registry = YamlCapabilityRepository(settings.capabilities_path)
    embedding_gateway = _embedding_gateway(settings, retry_policy)
    connections = settings.data_connections
    # ADR 0008, decision 5: a forwarding capability whose connection
    # also defines a static api_key is a startup config error — two
    # identities for one call. Fail fast here, where both configs are
    # in hand, rather than at first execution.
    validate_forwarding_conflicts(registry.list_all(), connections)
    executors = {
        "http": HttpCapabilityExecutor(connections, retry_policy),
        "snowflake": SnowflakeCapabilityExecutor(connections, retry_policy),
        "sqlserver": SqlServerCapabilityExecutor(connections, retry_policy),
    }
    try:
        embedding_cache = SqliteEmbeddingCache(settings.embedding_cache_path)
    except Exception as exc:  # noqa: BLE001 — discovery works uncached, just slower on cold start
        print(f"warning: embedding cache unavailable ({exc}); discovery will re-embed each restart",
              file=sys.stderr)
        embedding_cache = None
    return ToolDeps(
        capability_registry=registry,
        embedding_gateway=embedding_gateway,
        embedding_cache=embedding_cache,
        capability_executors=executors,
    )


def build_app(settings: Settings | None = None) -> FastAPI:
    """Construct the ASGI app without starting a server.

    main() runs it under uvicorn; the Lambda inbound adapter
    (controllers/lambda_handler.py) needs the same app object without a
    server process, so construction lives here, not in main().
    """
    settings = settings or Settings.from_env()
    _validate_lambda_settings(settings)
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
    )
    deps = build_tool_deps(settings, retry_policy)
    tools = load_tools(deps)
    system_prompt = build_system_prompt(tool_names(tools))
    # No gateway is built here: the factory builds (and caches) one per
    # allowlist key on first use, so a listed provider without
    # credentials never fails startup — the first request for that key
    # surfaces it as a 502. Embeddings stay on the configured provider
    # path regardless of the per-request chat model (ADR 0007).
    factory = ModelGatewayFactory(settings, retry_policy, tools, system_prompt)
    conversations = build_repository(settings)
    service = AskAgentService(
        conversations=conversations,
        gateway_resolver=factory,
        embedding_gateway=deps.embedding_gateway,
        capability_registry=deps.capability_registry,
    )
    return create_app(
        service, model_label=model_label(settings),
        cors_origins=settings.cors_origins,
        model_keys=factory.available_keys,
        token_validator=_build_token_validator(settings),
    )
