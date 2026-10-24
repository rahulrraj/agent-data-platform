"""Bedrock implementation of the ModelGateway port (<Technology><PortName>, §2).

Same stateless-per-call shape as GeminiModelGateway; only the model differs.
Credentials come from the standard boto3 chain (env vars, shared config file,
IAM role) — this adapter never handles credentials itself.
"""

from __future__ import annotations

from agent_api.adapters.strands_base import StrandsGatewayBase
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.model_gateway import ModelGatewayError

# Bedrock error codes worth retrying (botocore ClientError shape).
_TRANSIENT_CODES = (
    "ThrottlingException",
    "Throttling",
    "TooManyRequestsException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "InternalServerError",
)


class BedrockModelGateway(StrandsGatewayBase):
    """ModelGateway backed by Amazon Bedrock via Strands' BedrockModel."""

    def __init__(
        self,
        region_name: str,
        model_id: str,
        *,
        tools: list,
        system_prompt: str,
        retry_policy: RetryPolicy,
    ) -> None:
        super().__init__(tools=tools, system_prompt=system_prompt, retry_policy=retry_policy)
        self._region_name = region_name
        self._model_id = model_id

    def _provider_name(self) -> str:
        return "bedrock"

    def _build_model(self):
        # Local import: only this adapter needs the Bedrock provider package.
        from strands.models import BedrockModel

        return BedrockModel(
            region_name=self._region_name,
            model_id=self._model_id,
        )

    def _is_transient(self, exc: Exception) -> bool:
        # Strands translates Bedrock throttling into its own exception type.
        try:
            from strands.types.exceptions import ModelThrottledException

            if isinstance(exc, ModelThrottledException):
                return True
        except ImportError:
            pass
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

    def ping(self) -> None:
        """Cheap readiness check: AWS credentials resolve.

        A live inference ping would burn tokens on every probe, so readiness
        here means 'configured' — same as the Gemini adapter. The first real
        call proves the rest.
        """
        try:
            import boto3
        except ImportError as exc:
            raise ModelGatewayError("bedrock not reachable: boto3 is not installed") from exc
        session = boto3.Session(region_name=self._region_name)
        if session.get_credentials() is None:
            raise ModelGatewayError(
                "bedrock not reachable: no AWS credentials found "
                "(set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or use an IAM role)"
            )
