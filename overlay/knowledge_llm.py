"""LLM backend seam for the advisory knowledge base.

The knowledge base can be enriched by a large language model (Claude on Amazon
Bedrock) so that step recommendations are critiqued and expressed well, grounded
in real solved-ticket history. This module isolates the model behind a tiny
protocol so that:

* nothing else imports boto3, and the whole feature runs and is tested offline
  against a deterministic fake backend; and
* the production backend is a config-driven Bedrock model id, so "the newest
  Claude available in your region" is a one-line change, never a code rewrite.

CRITICAL BOUNDARY: the model is ADVISORY. It drafts and critiques step
recommendations; it never approves a chunk, never executes an action, and never
changes which procedure the matcher authoritatively picks. Its output is text a
human reads before deciding. Enforcement of that boundary lives in the service
and the action gateway, not here -- this module only turns a prompt into text.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

# Default model id. A placeholder that resolves to whatever the deployment sets
# via BEDROCK_MODEL_ID; the code never hard-depends on a specific generation.
# Default to the EU regional inference profile for Claude Opus 5, which is
# access-enabled in this account/region. Newer Claude models require an
# inference-profile id (the "eu." / "global." prefix), not a bare model id.
DEFAULT_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.anthropic.claude-opus-5")
DEFAULT_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-1"


class LLMUnavailable(Exception):
    """The configured LLM backend could not be reached or is not configured."""


class LLMBackend(Protocol):
    """Turns a system+user prompt into text. Nothing more."""

    @property
    def name(self) -> str: ...

    def complete(self, system: str, user: str, *, max_tokens: int = 1200) -> str: ...


class EchoBackend:
    """Deterministic offline backend. No network, no model.

    It does NOT invent a fix. It echoes a structured summary of the grounding it
    was given, so the feature is fully exercisable (and testable) without
    Bedrock, and so a local user plainly sees "no live model is attached; this is
    a structured view of the retrieved history" rather than a hallucinated plan.
    """

    @property
    def name(self) -> str:
        return "echo (offline; no live model attached)"

    def complete(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
        return (
            "[OFFLINE ADVISORY — no live model attached]\n"
            "A grounded recommendation requires a configured Bedrock model "
            "(set BEDROCK_MODEL_ID and provide AWS credentials). The retrieved "
            "grounding below is real; the synthesis step is skipped offline.\n"
        )


class BedrockBackend:
    """Calls a Claude model on Amazon Bedrock via the Converse API.

    boto3 is imported lazily so importing this module never requires it. Any
    failure (no creds, model not enabled, throttling) raises LLMUnavailable, and
    the caller degrades to the offline echo rather than breaking the request.
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        region_name: str | None = None,
        client: Any = None,
    ) -> None:
        self.model_id = model_id or DEFAULT_MODEL_ID
        self._region = region_name or DEFAULT_REGION
        self._client = client

    @property
    def name(self) -> str:
        return f"bedrock:{self.model_id}"

    def _bedrock(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # lazy

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
            return self._client
        except Exception as exc:  # noqa: BLE001
            raise LLMUnavailable(f"could not create Bedrock client: {exc}") from exc

    def complete(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
        client = self._bedrock()
        try:
            # Note: newer Claude models reject `temperature` in inferenceConfig,
            # so we deliberately omit it and rely on the model default.
            resp = client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens},
            )
        except Exception as exc:  # noqa: BLE001  (botocore errors, throttling, etc.)
            raise LLMUnavailable(f"Bedrock call failed: {exc}") from exc
        # Find the first text block. Newer models may emit non-text content
        # blocks (e.g. reasoning) before the text, so do not assume index 0.
        try:
            blocks = resp["output"]["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMUnavailable("unexpected Bedrock response shape") from exc
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                return block["text"]
        raise LLMUnavailable("Bedrock response contained no text block")


def llm_enabled() -> bool:
    """A live model is used only when explicitly enabled."""

    return os.getenv("LOKI_LLM_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def make_backend(*, client: Any = None) -> LLMBackend:
    """Pick the backend: Bedrock when enabled, else the offline echo.

    Defaulting to echo keeps the feature safe and free: it never silently makes
    a paid model call or a network request unless LOKI_LLM_ENABLED is set.
    """

    if llm_enabled() or client is not None:
        return BedrockBackend(client=client)
    return EchoBackend()
