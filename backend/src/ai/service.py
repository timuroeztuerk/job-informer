"""One local coordinator, at most 100 asynchronous Flex requests."""

import asyncio
import fcntl
import json
import os
import random
import time
from email.utils import parsedate_to_datetime

import openai
from pydantic import ValidationError

from .models import JobExtraction, validate_evidence
from .prompt import MODEL, PROMPT, REASONING, contract
from .store import AIStore

CONCURRENCY = 100
MAX_ATTEMPTS = 5


class OutputError(ValueError):
    pass


def response_metadata(response, elapsed):
    usage = response.usage
    metadata = {"request_id": getattr(response, "_request_id", None), "response_id": response.id,
            "model": response.model, "service_tier": response.service_tier,
            "latency_ms": round(elapsed*1000), "usage": {
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "cached_tokens": getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0,
                "reasoning_tokens": getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0}}
    # GPT-5.4 mini Flex USD / 1M tokens, verified 2026-09-05. Reasoning is included in output.
    if response.service_tier == "flex" and response.model.startswith("gpt-5.4-mini") and usage:
        tokens = metadata["usage"]
        metadata["estimated_cost_usd"] = ((tokens["input_tokens"]-tokens["cached_tokens"])*0.375
                                          + tokens["cached_tokens"]*0.0375 + tokens["output_tokens"]*2.25)/1_000_000
    return metadata


class AIService:
    def __init__(self, db_path, *, requester=None):
        self.store = AIStore(db_path)
        self.requester = requester
        self.client = None
        self.task = None
        self.closed = False
        self.db_lock = asyncio.Lock()
        self.lock_file = open(str(db_path)+".ai.lock", "a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            raise RuntimeError("An AI coordinator is already running for this database.") from None
        self.store.recover()

    async def db(self, method, *args, **kwargs):
        async with self.db_lock:
            return await asyncio.to_thread(method, *args, **kwargs)

    @property
    def configured(self):
        return bool(self.requester or os.getenv("OPENAI_API_KEY"))

    def start(self):
        if not self.configured:
            raise ValueError("OPENAI_API_KEY is not configured on the backend.")
        if self.closed:
            raise ValueError("AI extraction is shutting down.")
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def queue(self, retry=False):
        if not self.configured:
            raise ValueError("OPENAI_API_KEY is not configured on the backend.")
        added = await self.db(self.store.retry_failed if retry else self.store.enqueue)
        self.start()
        return {**await self.status(), "added": added}

    async def status(self):
        return {**await self.db(self.store.status), "configured": self.configured}

    async def control(self, action):
        if action == "pause":
            await self.db(self.store.pause)
        else:
            if not self.configured:
                raise ValueError("OPENAI_API_KEY is not configured on the backend.")
            await self.db(self.store.resume)
            self.start()
        return await self.status()

    async def request(self, work):
        if self.requester:
            return await self.requester(work)
        if self.client is None:
            self.client = openai.AsyncOpenAI(timeout=900.0, max_retries=0)
        return await self.client.responses.parse(
            model=MODEL, reasoning={"effort": REASONING}, service_tier="flex", store=False,
            instructions=PROMPT, input=work["input_json"], text_format=JobExtraction,
            # No max_output_tokens: the user's requested uncapped application output.
        )

    async def process(self, work):
        start = time.monotonic()
        metadata = {}
        draft = None
        try:
            if json.loads(work["contract_json"]) != contract():
                raise OutputError("Extraction settings changed. Queue the selected jobs again to use the current version.")
            response = await self.request(work)
            metadata = response_metadata(response, time.monotonic()-start)
            if response.status != "completed":
                raise OutputError("AI response was incomplete; no result was published.")
            if response.output_parsed is None:
                raise OutputError("AI returned a refusal or no structured result; no result was published.")
            draft = response.output_parsed.model_dump()
            parsed = JobExtraction.model_validate(response.output_parsed)
            payload = validate_evidence(parsed, json.loads(work["input_json"])["sources"])
            await self.db(self.store.finish, work, payload=payload, metadata=metadata)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = getattr(error, "status_code", None)
            body = getattr(error, "body", None) or {}
            api_error = body.get("error", body) if isinstance(body, dict) else {}
            quota = isinstance(api_error, dict) and api_error.get("code") in {"insufficient_quota", "billing_hard_limit_reached"}
            retryable = not quota and (isinstance(error, (openai.APITimeoutError, openai.APIConnectionError)) or code in {408, 409, 429} or (code is not None and code >= 500))
            kind = "quota" if quota else "rate_limit" if code == 429 else "network" if isinstance(error, openai.APIConnectionError) else "output" if isinstance(error, (ValueError, ValidationError)) else "api" if code else "internal"
            message = {"quota": "OpenAI quota or billing needs attention.", "rate_limit": "Flex capacity or rate limit reached; queued work will wait.",
                       "network": "Connection failed or timed out; remote completion may be unknown.",
                       "output": "Output did not pass structure or source-evidence checks. Saved text is unchanged.",
                       "api": f"OpenAI request failed (HTTP {code}). Check model access or API configuration.",
                       "internal": "AI extraction stopped after an internal error."}[kind]
            if isinstance(error, OutputError):
                message = str(error)
            elif isinstance(error, ValidationError):
                problems = [{"field": ".".join(map(str, item["loc"])), "message": item["msg"]} for item in error.errors(include_input=False, include_url=False)]
                draft = {"validation_errors": problems, "output": draft}
                message = "Output structure failed validation: " + "; ".join(f"{item['field']}: {item['message']}" for item in problems[:3])
            elif isinstance(error, ValueError):
                message = str(error)
            delay = None
            if retryable and work["attempts"] < MAX_ATTEMPTS:
                delay = min(300, 5 * 2 ** (work["attempts"]-1)) + random.uniform(0, 2)
                header = getattr(getattr(error, "response", None), "headers", {}).get("retry-after")
                if header:
                    try:
                        delay = max(delay, float(header))
                    except ValueError:
                        try:
                            delay = max(delay, parsedate_to_datetime(header).timestamp()-time.time())
                        except (ValueError, TypeError, OverflowError):
                            pass
            metadata.setdefault("latency_ms", round((time.monotonic()-start)*1000))
            metadata.setdefault("request_id", getattr(error, "request_id", None))
            await self.db(self.store.finish, work, metadata=metadata, error_kind=kind, message=message,
                          retry_delay=delay, global_backoff=code == 429, draft=draft)
            if quota or code in {400, 401, 403, 404} or kind == "internal":
                await self.db(self.store.pause, message)

    async def run(self):
        active = set()
        try:
            while not self.closed:
                work = await self.db(self.store.claim, CONCURRENCY-len(active))
                active.update(asyncio.create_task(self.process(item)) for item in work)
                if not active:
                    status = await self.status()
                    if status["paused"] or not (status["counts"].get("queued") or status["counts"].get("retry_wait")):
                        return
                    await asyncio.sleep(1)
                    continue
                done, active = await asyncio.wait(active, timeout=1, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.db(self.store.pause, "AI worker stopped unexpectedly. Restart the app to recover saved work.")
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)

    async def close(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.db(self.store.recover)
        if self.client:
            await self.client.close()
        self.lock_file.close()
