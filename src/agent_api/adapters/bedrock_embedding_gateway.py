"""Bedrock embedding gateway (<Technology><PortName>, §2). Amazon Titan.

Titan's InvokeModel takes one text per call, so embed() invokes once per
input text. Credentials come from the standard boto3 chain (env vars, shared
config file, IAM role) — this adapter never handles credentials itself.
"""

from __future__ import annotations

import json


class BedrockEmbeddingGateway:
    """EmbeddingGateway backed by Amazon Titan Text Embeddings on Bedrock."""

    def __init__(self, region_name: str, model_id: str = "amazon.titan-embed-text-v2:0") -> None:
        self._region_name = region_name
        self._model_id = model_id

    @property
    def model_name(self) -> str:
        return self._model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Local import: only this adapter needs boto3.
        import boto3

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

    def ping(self) -> None:
        import boto3

        session = boto3.Session(region_name=self._region_name)
        if session.get_credentials() is None:
            raise RuntimeError(
                "no AWS credentials found "
                "(set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or use an IAM role)"
            )
