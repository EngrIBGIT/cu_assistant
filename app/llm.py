"""Language model adapter.

The point of this module is that it is *optional*. The product ships with no
model configured and remains fully functional, because the grounded answer is
assembled from retrieved source text rather than generated from a model's
weights. A model improves phrasing and handles multi-part questions; it is never
the thing that makes an answer true.

The interface is deliberately narrow — one method, plain text in and out — so
that a provider can be swapped without touching the composer. Swapping providers
is an ADR, not a code change scattered through the pipeline.

No request content is logged. That is a privacy control, not an omission: this
product collects no personal data, and logging user questions would create one.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT_S,
)


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    provider: str
    latency_ms: int
    truncated: bool = False


class LLMClient:
    """Thin client over a chat-completions style API.

    Deliberately implemented on the standard library rather than an SDK: the
    request shape is small, and an SDK would add a dependency tree to a product
    whose main selling point is how little it needs.
    """

    def __init__(
        self,
        provider: str = LLM_PROVIDER,
        model: str = LLM_MODEL,
        base_url: str = LLM_BASE_URL,
        api_key: str = LLM_API_KEY,
        timeout: float = LLM_TIMEOUT_S,
        max_tokens: int = LLM_MAX_TOKENS,
    ) -> None:
        self.provider = (provider or "").strip().lower()
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or os.getenv("CRA_LLM_API_KEY", "")
        self.timeout = timeout
        self.max_tokens = max_tokens

    @property
    def available(self) -> bool:
        if not self.provider:
            return False
        if self.provider == "ollama":
            return bool(self.base_url)
        return bool(self.api_key)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" if self.provider else "none"

    def _endpoint(self) -> str:
        if self.provider == "ollama":
            return f"{self.base_url}/api/chat"
        return f"{self.base_url or 'https://api.openai.com/v1'}/chat/completions"

    def _payload(self, system: str, user: str) -> dict:
        if self.provider == "ollama":
            return {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {"temperature": 0, "num_predict": self.max_tokens},
            }
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

    def complete(self, system: str, user: str) -> LLMResult | None:
        """Return a completion, or None if the model is unavailable or failed.

        Returning None rather than raising is intentional: a model outage must
        degrade the product to retrieval-only mode, never take it down. The
        health endpoint surfaces the degradation so it cannot pass unnoticed.
        """
        if not self.available:
            return None

        import time  # noqa: PLC0415 - only needed on the request path

        request = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(self._payload(system, user)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
            method="POST",
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None

        elapsed = int((time.perf_counter() - started) * 1000)

        try:
            if self.provider == "ollama":
                text = payload["message"]["content"]
            else:
                text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None

        if not isinstance(text, str) or not text.strip():
            return None

        return LLMResult(
            text=text.strip(),
            model=self.model,
            provider=self.provider,
            latency_ms=elapsed,
            truncated=self.provider != "ollama"
            and payload.get("choices", [{}])[0].get("finish_reason") == "length",
        )
