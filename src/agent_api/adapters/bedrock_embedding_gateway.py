"""Bedrock embedding gateway (<Technology><PortName>, §2). Amazon Titan.

Titan's InvokeModel takes one text per call, so embed() invokes once per
input text. Credentials come from the standard boto3 chain (env vars, shared
config file, IAM role) — this adapter never handles credentials itself.
"""

from __future__ import annotations

import json

from agent_api.platform.retry import RetryPolicy, call_with_retry
from agent_api.ports.embedding_gateway import (
    DEFAULT_SIMILARITY_THRESHOLD,
    EmbeddingGatewayError,
)

_TITAN_EMBED_TEXT_V2 = "amazon.titan-embed-text-v2:0"

# Same transient botocore codes as the Bedrock model gateway.
_TRANSIENT_CODES = (
    "ThrottlingException",
    "Throttling",
    "TooManyRequestsException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "InternalServerError",
)


class BedrockEmbeddingGateway:
    """EmbeddingGateway backed by Amazon Titan Text Embeddings on Bedrock."""

    def __init__(
        self,
        region_name: str,
        model_id: str = _TITAN_EMBED_TEXT_V2,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._region_name = region_name
        self._model_id = model_id
        self._retry = retry_policy or RetryPolicy()

    @property
    def model_name(self) -> str:
        return self._model_id

    @property
    def similarity_threshold(self) -> float:
        if self._model_id == _TITAN_EMBED_TEXT_V2:
            # PROVISIONAL VALUE — do not treat as calibrated. The 0.30
            # cutoff comes from a SINGLE observed true question scoring
            # exactly 0.30, with no margin and no false-positive
            # calibration (no unrelated/near-miss prompts measured for
            # this model). A Titan calibration run using the same
            # 15-prompt protocol as the Gemini calibration is still
            # needed before trusting 0.30; until then it only keeps a
            # known-true question from being rejected. Any other
            # Bedrock embedding model keeps the default cutoff.
            return 0.30
        return DEFAULT_SIMILARITY_THRESHOLD

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        try:
            import botocore.exceptions as boto_exc
        except ImportError:
            return False
        if isinstance(
            exc,
            (
                boto_exc.EndpointConnectionError,
                boto_exc.ConnectTimeoutError,
                boto_exc.ReadTimeoutError,
                boto_exc.ConnectionClosedError,
            ),
        ):
            return True
        if isinstance(exc, boto_exc.ClientError):
            code = (exc.response.get("Error") or {}).get("Code")
            return code in _TRANSIENT_CODES
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Local import: only this adapter needs boto3.
        import boto3

        def _embed_once() -> list[list[float]]:
            client = boto3.client("bedrock-runtime", region_name=self._region_name)
            vectors: list[list[float]] = []
            for text in texts:
                response = client.invoke_model(
                    modelId=self._model_id,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps({"inputText": text, "dimensions": 1024, "normalize": True}),
                )
                payload = json.loads(response["body"].read())
                vectors.append([float(v) for v in payload["embedding"]])
            return vectors

        try:
            return call_with_retry(
                _embed_once, policy=self._retry, is_transient=self._is_transient
            )
        except EmbeddingGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingGatewayError(f"bedrock embed failed: {exc}") from exc

    def ping(self) -> None:
        """Config-only check: credentials resolve (a live Titan call on
        every probe would burn tokens; same rationale as the Bedrock model
        gateway's config-only ping)."""
        try:
            import boto3

            session = boto3.Session(region_name=self._region_name)
            if session.get_credentials() is None:
                raise EmbeddingGatewayError(
                    "no AWS credentials found "
                    "(set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or use an IAM role)"
                )
        except EmbeddingGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingGatewayError(f"bedrock embed ping failed: {exc}") from exc
