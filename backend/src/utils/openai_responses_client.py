"""Shared OpenAI Responses API client for structured backend tasks."""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Generic, Optional, Protocol, Type, TypeVar

from loguru import logger
from openai import AsyncOpenAI
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
class LLMUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class LLMCallTelemetry:
    model: str
    status: str
    started_at: str
    finished_at: str
    latency_ms: float
    response_id: str | None = None
    retry_count: int = 0
    exhausted_retries: bool = False
    refusal_text: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    output_preview: str | None = None
    usage: LLMUsage = field(default_factory=LLMUsage)


@dataclass
class StructuredLLMResult(Generic[StructuredModel]):
    parsed: StructuredModel | None
    telemetry: LLMCallTelemetry


@dataclass
class _LLMStats:
    requests: int = 0
    retries: int = 0
    successes: int = 0
    failures: int = 0
    refusals: int = 0
    timeout_errors: int = 0
    rate_limit_errors: int = 0
    parse_errors: int = 0
    exhausted_retries: int = 0
    total_elapsed_ms: float = 0.0

    def copy(self) -> "_LLMStats":
        return _LLMStats(**self.__dict__)


class LLMStructuredClient(Protocol):
    api_key: str | None
    model: str

    async def parse_structured_async(
        self,
        *,
        response_model: Type[StructuredModel],
        input: str | list[dict[str, Any]],
        instructions: str | None = None,
        max_output_tokens: int = 2048,
        temperature: float | None = None,
    ) -> StructuredLLMResult[StructuredModel]:
        """Return a structured result backed by the OpenAI Responses API."""


class OpenAIResponsesClient:
    """Async Responses API client with retry/backoff and compact telemetry."""

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

        self._client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout_seconds) if self.api_key else None
        self._lock = threading.Lock()
        self._stats = _LLMStats()

        if not self.api_key:
            logger.warning("OpenAIResponsesClient initialized without API key; LLM calls will be skipped.")

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            stats = self._stats.copy()
            data = stats.__dict__.copy()

        requests = max(data["requests"], 1)
        data["success_rate"] = data["successes"] / requests
        data["failure_rate"] = data["failures"] / requests
        data["avg_elapsed_ms"] = data["total_elapsed_ms"] / max(1, data["successes"] + data["refusals"])
        data["model"] = self.model
        data["timeout_seconds"] = self.timeout_seconds
        data["max_retries"] = self.max_retries
        return data

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = _LLMStats()

    async def parse_structured_async(
        self,
        *,
        response_model: Type[StructuredModel],
        input: str | list[dict[str, Any]],
        instructions: str | None = None,
        max_output_tokens: int = 2048,
        temperature: float | None = None,
    ) -> StructuredLLMResult[StructuredModel]:
        """Call the Responses API and parse directly into a Pydantic model."""
        if not self._client:
            return self._error_result(
                error_type="MissingAPIKey",
                error_message="No LLM client configured.",
            )

        if not input:
            return self._error_result(
                error_type="EmptyInput",
                error_message="Empty input for structured LLM generation.",
            )

        attempt = 0
        max_attempts = self.max_retries + 1
        model_errors: list[str] = []
        while attempt < max_attempts:
            attempt += 1
            self._increment("requests")
            started_at = datetime.now(UTC)
            started = monotonic()

            try:
                request_kwargs: dict[str, Any] = {
                    "model": self.model,
                    "input": input,
                    "text_format": response_model,
                    "max_output_tokens": max_output_tokens,
                    "store": False,
                }
                if instructions:
                    request_kwargs["instructions"] = instructions
                if temperature is not None and self._supports_temperature():
                    request_kwargs["temperature"] = temperature

                response = await self._client.responses.parse(**request_kwargs)

                elapsed_ms = (monotonic() - started) * 1000
                refusal_text = self._extract_refusal_text(response)
                usage = self._extract_usage(response)
                response_id = self._extract_response_id(response)

                if refusal_text:
                    telemetry = LLMCallTelemetry(
                        model=self.model,
                        status="refusal",
                        started_at=started_at.isoformat(),
                        finished_at=datetime.now(UTC).isoformat(),
                        latency_ms=max(0.0, elapsed_ms),
                        response_id=response_id,
                        retry_count=attempt - 1,
                        exhausted_retries=False,
                        refusal_text=refusal_text,
                        output_preview=self._truncate_text(refusal_text),
                        usage=usage,
                    )
                    self._record_terminal_result("refusals", telemetry.latency_ms)
                    return StructuredLLMResult(parsed=None, telemetry=telemetry)

                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    raise ValueError("No parsed structured output returned from LLM.")

                telemetry = LLMCallTelemetry(
                    model=self.model,
                    status="success",
                    started_at=started_at.isoformat(),
                    finished_at=datetime.now(UTC).isoformat(),
                    latency_ms=max(0.0, elapsed_ms),
                    response_id=response_id,
                    retry_count=attempt - 1,
                    exhausted_retries=False,
                    output_preview=self._build_output_preview(parsed, response),
                    usage=usage,
                )
                self._record_terminal_result("successes", telemetry.latency_ms)
                return StructuredLLMResult(parsed=parsed, telemetry=telemetry)

            except Exception as exc:
                elapsed_ms = (monotonic() - started) * 1000
                self._record_failure(exc, elapsed_ms)
                model_errors.append(f"{type(exc).__name__}: {exc}")

                should_retry = attempt < max_attempts and self._is_retryable(exc)
                if not should_retry:
                    logger.error("Responses API request exhausted: {}", " | ".join(model_errors))
                    exhausted_retries = self._is_retryable(exc) and attempt > 1
                    if exhausted_retries:
                        self._increment("exhausted_retries")
                    return StructuredLLMResult(
                        parsed=None,
                        telemetry=LLMCallTelemetry(
                            model=self.model,
                            status="failed",
                            started_at=started_at.isoformat(),
                            finished_at=datetime.now(UTC).isoformat(),
                            latency_ms=max(0.0, elapsed_ms),
                            retry_count=attempt - 1,
                            exhausted_retries=exhausted_retries,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        ),
                    )

                delay = self._compute_backoff(attempt)
                self._increment("retries")
                logger.warning(
                    "Responses API request attempt {}/{} failed ({}: {}), retrying in {:.2f}s",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        return self._error_result(error_type="RuntimeError", error_message="Unexpected Responses API exhaustion.")

    def parse_structured(
        self,
        *,
        response_model: Type[StructuredModel],
        input: str | list[dict[str, Any]],
        instructions: str | None = None,
        max_output_tokens: int = 2048,
        temperature: float | None = None,
    ) -> StructuredLLMResult[StructuredModel]:
        """Sync wrapper for non-async call sites."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.parse_structured_async(
                    response_model=response_model,
                    input=input,
                    instructions=instructions,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
            )

        raise RuntimeError("Use parse_structured_async inside an active event loop.")

    def _supports_temperature(self) -> bool:
        model_name = (self.model or "").lower()
        return not any(tag in model_name for tag in ("gpt-5", "o1"))

    def _compute_backoff(self, attempt: int) -> float:
        exponent = max(1, attempt - 1)
        delay = min(self.retry_base_delay * (2**exponent), self.retry_max_delay)
        if self.retry_jitter <= 0:
            return delay
        span = delay * self.retry_jitter
        return random.uniform(max(0.0, delay - span), delay + span)

    def _is_retryable(self, error: Exception) -> bool:
        if isinstance(error, (APITimeoutError, APIConnectionError, RateLimitError)):
            return True

        status_code = getattr(error, "status_code", None)
        if isinstance(status_code, int) and status_code >= 500:
            return True

        msg = (str(error) or "").lower()
        if "timeout" in msg or "connection" in msg:
            return True
        if "no parsed structured output returned from llm" in msg:
            return True
        return False

    @staticmethod
    def _extract_response_id(response: Any) -> str | None:
        response_id = getattr(response, "id", None)
        if response_id:
            return str(response_id)
        if isinstance(response, dict):
            value = response.get("id")
            return str(value) if value else None
        return None

    @classmethod
    def _extract_usage(cls, response: Any) -> LLMUsage:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")

        input_tokens = cls._coerce_optional_int(cls._read_attr(usage, "input_tokens"))
        output_tokens = cls._coerce_optional_int(cls._read_attr(usage, "output_tokens"))
        total_tokens = cls._coerce_optional_int(cls._read_attr(usage, "total_tokens"))

        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    @classmethod
    def _extract_refusal_text(cls, response: Any) -> str | None:
        direct_refusal = cls._read_attr(response, "refusal")
        if direct_refusal:
            return str(direct_refusal).strip() or None

        output_items = cls._read_attr(response, "output") or []
        for item in output_items if isinstance(output_items, list) else [output_items]:
            content_items = cls._read_attr(item, "content") or []
            for content in content_items if isinstance(content_items, list) else [content_items]:
                item_type = str(cls._read_attr(content, "type") or "").strip().lower()
                if item_type == "refusal":
                    text = cls._read_attr(content, "refusal") or cls._read_attr(content, "text") or cls._read_attr(content, "value")
                    if isinstance(text, dict):
                        text = text.get("value") or text.get("text")
                    if text:
                        return str(text).strip() or None
                refusal = cls._read_attr(content, "refusal")
                if refusal:
                    return str(refusal).strip() or None
        return None

    @classmethod
    def _extract_output_text(cls, response: Any) -> str | None:
        output_text = cls._read_attr(response, "output_text")
        if output_text:
            return str(output_text).strip() or None

        output_items = cls._read_attr(response, "output") or []
        for item in output_items if isinstance(output_items, list) else [output_items]:
            content_items = cls._read_attr(item, "content") or []
            for content in content_items if isinstance(content_items, list) else [content_items]:
                item_type = str(cls._read_attr(content, "type") or "").strip().lower()
                if item_type not in {"output_text", "text"}:
                    continue
                text = cls._read_attr(content, "text") or cls._read_attr(content, "value")
                if isinstance(text, dict):
                    text = text.get("value") or text.get("text")
                if text:
                    return str(text).strip() or None
        return None

    def _build_output_preview(self, parsed: BaseModel, response: Any) -> str | None:
        text = self._extract_output_text(response)
        if text:
            return self._truncate_text(text)

        try:
            payload = parsed.model_dump(by_alias=True)
            return self._truncate_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return None

    @staticmethod
    def _truncate_text(value: str | None, *, limit: int = 500) -> str | None:
        if not value:
            return None
        clean = " ".join(str(value).split())
        if not clean:
            return None
        if len(clean) <= limit:
            return clean
        return f"{clean[: limit - 3]}..."

    @staticmethod
    def _read_attr(value: Any, key: str) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _increment(self, key: str) -> None:
        with self._lock:
            current = getattr(self._stats, key, None)
            if current is None:
                return
            setattr(self._stats, key, current + 1)

    def _record_terminal_result(self, key: str, elapsed_ms: float) -> None:
        with self._lock:
            setattr(self._stats, key, getattr(self._stats, key) + 1)
            self._stats.total_elapsed_ms += max(0.0, elapsed_ms)

    def _record_failure(self, error: Exception, elapsed_ms: float) -> None:
        with self._lock:
            self._stats.failures += 1
            self._stats.total_elapsed_ms += max(0.0, elapsed_ms)

        if isinstance(error, APITimeoutError):
            self._increment("timeout_errors")
        elif isinstance(error, RateLimitError):
            self._increment("rate_limit_errors")

        if isinstance(error, ValueError):
            self._increment("parse_errors")

    def _error_result(self, *, error_type: str, error_message: str) -> StructuredLLMResult[Any]:
        now = datetime.now(UTC).isoformat()
        self._increment("failures")
        return StructuredLLMResult(
            parsed=None,
            telemetry=LLMCallTelemetry(
                model=self.model,
                status="failed",
                started_at=now,
                finished_at=now,
                latency_ms=0.0,
                error_type=error_type,
                error_message=error_message,
            ),
        )
