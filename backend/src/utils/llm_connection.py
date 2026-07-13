"""Backward-compatible wrapper around the shared Responses API client."""

from __future__ import annotations

from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from .openai_responses_client import OpenAIResponsesClient


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LLMConnection:
    """Deprecated compatibility wrapper.

    New code should use ``OpenAIResponsesClient`` directly. This adapter keeps
    older call sites and tests stable while the shared Responses API client
    becomes the single implementation.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str],
        model: str,
        timeout_seconds: float = 45.0,
        max_retries: int = 3,
        retry_base_delay: float = 0.75,
        retry_max_delay: float = 8.0,
        retry_jitter: float = 0.2,
    ) -> None:
        self._client = OpenAIResponsesClient(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
            retry_jitter=retry_jitter,
        )
        self.api_key = self._client.api_key
        self.model = self._client.model

    def get_stats(self) -> dict:
        return self._client.get_stats()

    def reset_stats(self) -> None:
        self._client.reset_stats()

    def generate_structured(
        self,
        prompt: str,
        response_model: Optional[Type[StructuredModel]],
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
    ):
        if response_model is None:
            raise ValueError("response_model is required for the compatibility wrapper.")
        result = self._client.parse_structured(
            response_model=response_model,
            input=prompt,
            instructions=system,
            temperature=temperature,
        )
        return result.parsed
