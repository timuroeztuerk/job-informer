"""Utilities for talking to LLM providers with retry and lightweight observability."""

from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass
from time import monotonic
from typing import Any, Optional, Type, TypeVar

from loguru import logger
from openai import OpenAI
from pydantic import BaseModel

try:  # pragma: no cover - compatibility across OpenAI patch levels
    from openai import APIConnectionError, APITimeoutError, RateLimitError
except Exception:  # pragma: no cover - fallback if exception classes move names
    class _OpenAIUnavailableError(Exception):
        pass

    APIConnectionError = _OpenAIUnavailableError
    APITimeoutError = _OpenAIUnavailableError
    RateLimitError = _OpenAIUnavailableError


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


@dataclass
class _LLMStats:
    requests: int = 0
    retries: int = 0
    successes: int = 0
    failures: int = 0
    timeout_errors: int = 0
    rate_limit_errors: int = 0
    parse_errors: int = 0
    exhausted_retries: int = 0
    total_elapsed_ms: float = 0.0

    def copy(self) -> "_LLMStats":
        return _LLMStats(**self.__dict__)


class LLMConnection:
    """Small OpenAI-compatible wrapper used by parser and AI purge.

    Provides:
    - bounded retries with exponential backoff + jitter
    - per-instance request metrics
    - structured JSON parsing into Pydantic models when provided
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
        self.api_key = (api_key or "").strip() or None
        self.model = model or "gpt-5-mini"
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.retry_max_delay = max(self.retry_base_delay, float(retry_max_delay))
        self.retry_jitter = min(1.0, max(0.0, float(retry_jitter)))

        self._client = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds) if self.api_key else None
        self._lock = threading.Lock()
        self._stats = _LLMStats()

        if not self.api_key:
            logger.warning("LLMConnection initialized without API key; LLM calls will be skipped.")

    def get_stats(self) -> dict[str, Any]:
        """Return a shallow copy of request statistics."""
        with self._lock:
            stats = self._stats.copy()
            data = stats.__dict__.copy()

        requests = max(data["requests"], 1)
        data["success_rate"] = data["successes"] / requests
        data["failure_rate"] = data["failures"] / requests
        data["avg_elapsed_ms"] = data["total_elapsed_ms"] / data["successes"] if data["successes"] else 0.0
        data["model"] = self.model
        data["timeout_seconds"] = self.timeout_seconds
        data["max_retries"] = self.max_retries
        return data

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = _LLMStats()

    def generate_structured(
        self,
        prompt: str,
        response_model: Optional[Type[StructuredModel]],
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> Optional[str | StructuredModel]:
        """Generate JSON from an LLM prompt and return either a validated model or raw JSON."""
        if not self._client:
            logger.debug("No LLM client configured; skipping generation.")
            return None

        if not prompt:
            logger.error("Empty prompt for LLM generation.")
            return None

        attempt = 0
        max_attempts = self.max_retries + 1
        model_errors: list[str] = []
        while attempt < max_attempts:
            attempt += 1
            self._increment("requests")
            started = monotonic()

            try:
                messages = [{"role": "user", "content": prompt}]
                if system:
                    messages.insert(0, {"role": "system", "content": system})

                request_kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                }

                if self._supports_max_completion_tokens():
                    request_kwargs["max_completion_tokens"] = max_tokens
                else:
                    request_kwargs["max_tokens"] = max_tokens

                if self._supports_temperature():
                    request_kwargs["temperature"] = temperature

                response = self._create_completion_with_fallback(request_kwargs=request_kwargs, max_tokens=max_tokens)

                content = self._extract_message_content(response)
                if content is None:
                    refusal = self._extract_message_refusal(response)
                    if refusal:
                        raise ValueError(f"LLM refusal: {refusal}")
                    raise ValueError("No message content returned from LLM.")

                elapsed_ms = (monotonic() - started) * 1000
                self._record_success(elapsed_ms)

                parsed = self._coerce_response(content=content, response_model=response_model)
                if parsed is None:
                    raise ValueError("LLM output was not parseable as JSON.")
                return parsed

            except Exception as exc:
                elapsed_ms = (monotonic() - started) * 1000
                self._record_failure(exc, elapsed_ms)
                model_errors.append(f"{type(exc).__name__}: {exc}")

                if attempt >= max_attempts or not self._is_retryable(exc):
                    logger.error("LLM request exhausted: {}", " | ".join(model_errors))
                    self._increment("exhausted_retries")
                    return None

                delay = self._compute_backoff(attempt)
                self._increment("retries")
                logger.warning(
                    "LLM request attempt {}/{} failed ({}: {}), retrying in {:.2f}s",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                time.sleep(delay)

        return None

    def _supports_max_completion_tokens(self) -> bool:
        model_name = (self.model or "").lower()
        return "gpt-5" in model_name or "o1" in model_name

    def _supports_temperature(self) -> bool:
        model_name = (self.model or "").lower()
        unsupported = ("gpt-5", "o1")
        return not any(tag in model_name for tag in unsupported)

    def _create_completion_with_fallback(self, request_kwargs: dict[str, Any], max_tokens: int):
        last_error: Optional[Exception] = None

        for _ in range(2):
            try:
                return self._client.chat.completions.create(**request_kwargs)
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "unsupported parameter" in message or "unsupported value" in message:
                    if "max_tokens" in request_kwargs:
                        request_kwargs.pop("max_tokens", None)
                        request_kwargs["max_completion_tokens"] = max_tokens
                        logger.warning("LLM request retried with max_completion_tokens after max_tokens mismatch.")
                        continue
                    if "max_completion_tokens" in request_kwargs:
                        request_kwargs.pop("max_completion_tokens", None)
                        request_kwargs["max_tokens"] = max_tokens
                        logger.warning("LLM request retried with max_tokens after max_completion_tokens mismatch.")
                        continue

                if "unsupported value" in message and "temperature" in message:
                    request_kwargs.pop("temperature", None)
                    logger.warning("LLM request retried without temperature after unsupported value error.")
                    continue

                raise

        if last_error:
            raise last_error
        raise RuntimeError("LLM request failed unexpectedly.")

    def _compute_backoff(self, attempt: int) -> float:
        """Compute exponential backoff (with jitter) based on retry attempt number."""
        exponent = max(1, attempt - 1)
        delay = min(self.retry_base_delay * (2**exponent), self.retry_max_delay)
        if self.retry_jitter <= 0:
            return delay
        span = delay * self.retry_jitter
        return random.uniform(max(0.0, delay - span), delay + span)

    def _is_retryable(self, error: Exception) -> bool:
        if isinstance(error, (APITimeoutError, APIConnectionError, RateLimitError)):
            return True

        msg = (str(error) or "").lower()
        if isinstance(error, ValueError):
            if "no message content returned from llm" in msg:
                return True
            if "llm refusal" in msg:
                return False

        status_code = getattr(error, "status_code", None)
        if isinstance(status_code, int) and status_code >= 500:
            return True

        if "timeout" in msg or "connection" in msg:
            return True
        return False

    def _extract_message_content(self, response: Any) -> Optional[str]:
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            return None
        content = getattr(message, "content", None)
        if isinstance(content, str):
            text = content.strip()
            return text or None
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = self._extract_content_part_text(item)
                if text:
                    parts.append(text)
            if parts:
                return "\n".join(parts).strip()
        return None

    @staticmethod
    def _extract_content_part_text(item: Any) -> Optional[str]:
        if item is None:
            return None
        if isinstance(item, str):
            text = item.strip()
            return text or None

        item_type = None
        if isinstance(item, dict):
            item_type = item.get("type")
            text_value = item.get("text")
            if isinstance(text_value, dict):
                text_value = text_value.get("value") or text_value.get("text")
            if text_value is None and item_type in {"output_text", "text"}:
                text_value = item.get("value")
        else:
            item_type = getattr(item, "type", None)
            text_value = getattr(item, "text", None)
            if text_value is None:
                nested_text = getattr(item, "value", None)
                if nested_text is not None:
                    text_value = nested_text
            if hasattr(text_value, "value"):
                text_value = getattr(text_value, "value", None)

        if item_type not in {None, "text", "output_text"}:
            return None
        if text_value is None:
            return None

        text = str(text_value).strip()
        return text or None

    def _extract_message_refusal(self, response: Any) -> Optional[str]:
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            return None
        refusal = getattr(message, "refusal", None)
        if refusal:
            return str(refusal).strip()
        return None

    def _coerce_response(
        self,
        *,
        content: str,
        response_model: Optional[Type[StructuredModel]],
    ) -> Optional[str | StructuredModel]:
        json_text = self._extract_json_text(content)
        if not json_text:
            return None

        try:
            json.loads(json_text)
        except json.JSONDecodeError:
            logger.debug("LLM response did not contain valid JSON: {}", json_text[:240])
            self._increment("parse_errors")
            return None

        if response_model is None:
            return json_text

        if (
            isinstance(response_model, type)
            and issubclass(response_model, BaseModel)
            and self._looks_like_json_object(json_text)
        ):
            try:
                payload = json.loads(json_text)
                return response_model.model_validate(payload)
            except Exception as exc:
                self._increment("parse_errors")
                payload_keys = []
                payload_preview = json_text[:400]
                try:
                    if isinstance(payload, dict):
                        payload_keys = sorted(str(key) for key in payload.keys())
                        payload_preview = json.dumps(payload, ensure_ascii=True)[:400]
                except Exception:
                    pass
                logger.warning(
                    "LLM output did not match structured model {}: {} | keys={} | preview={}",
                    getattr(response_model, "__name__", str(response_model)),
                    exc,
                    payload_keys,
                    payload_preview,
                )
                return json_text

        return json_text

    @staticmethod
    def _looks_like_json_object(text: str) -> bool:
        trimmed = (text or "").strip()
        return trimmed.startswith("{") and trimmed.endswith("}")

    @staticmethod
    def _extract_json_text(raw: str) -> str:
        if not raw:
            return ""

        text = raw.strip()

        fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
        if fenced_match:
            text = fenced_match.group(1).strip()

        if text.startswith("{") or text.startswith("["):
            return text.strip()

        start_obj = text.find("{")
        end_obj = text.rfind("}")
        if start_obj != -1 and end_obj > start_obj:
            return text[start_obj : end_obj + 1].strip()

        start_arr = text.find("[")
        end_arr = text.rfind("]")
        if start_arr != -1 and end_arr > start_arr:
            return text[start_arr : end_arr + 1].strip()

        return ""

    def _increment(self, key: str) -> None:
        with self._lock:
            current = getattr(self._stats, key, None)
            if current is None:
                return
            setattr(self._stats, key, current + 1)

    def _record_success(self, elapsed_ms: float) -> None:
        with self._lock:
            self._stats.successes += 1
            self._stats.total_elapsed_ms += max(0.0, elapsed_ms)

    def _record_failure(self, error: Exception, elapsed_ms: float) -> None:
        with self._lock:
            self._stats.failures += 1
            self._stats.total_elapsed_ms += max(0.0, elapsed_ms)

        if isinstance(error, APITimeoutError):
            self._increment("timeout_errors")
        elif isinstance(error, RateLimitError):
            self._increment("rate_limit_errors")
