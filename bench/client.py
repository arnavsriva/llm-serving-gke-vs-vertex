"""Async streaming client for OpenAI-compatible ``/v1/completions`` and ``/v1/chat/completions``.

TTFT is taken when the first chunk carrying generated text arrives: vLLM's leading chat chunk
(role only, empty content) does not count, while Qwen3 ``reasoning_content`` does, because those
are generated tokens too. Failures are recorded on the :class:`RequestRecord`, never raised, so
one bad response cannot abort a sweep.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from bench.records import RequestRecord
from bench.sse import SSEParser
from bench.workload import RequestSpec

API_PATHS = {"completions": "/v1/completions", "chat": "/v1/chat/completions"}
_ERROR_MAX_CHARS = 300


@dataclass(frozen=True)
class Endpoint:
    url: str
    api: str
    model: str
    headers: Mapping[str, str] = field(default_factory=dict)
    temperature: float = 0.0
    stream_usage: bool = True
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.api not in API_PATHS:
            raise ValueError(f"api must be one of {sorted(API_PATHS)}, got {self.api!r}")

    def payload(self, spec: RequestSpec) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": spec.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }
        if self.api == "chat":
            body["messages"] = [{"role": "user", "content": spec.prompt}]
        else:
            body["prompt"] = spec.prompt
        if self.stream_usage:
            body["stream_options"] = {"include_usage": True}
        body.update(self.extra_body)
        return body


def endpoint_url(base_url: str, api: str, path: str | None = None) -> str:
    """Join the server root and the API path (``path`` overrides the default for ``api``)."""
    suffix = API_PATHS[api] if path is None else "/" + path.lstrip("/")
    return base_url.rstrip("/") + suffix


async def send_request(
    session: aiohttp.ClientSession,
    endpoint: Endpoint,
    spec: RequestSpec,
    record: RequestRecord,
    now: Callable[[], float],
) -> None:
    """Send one streaming request and fill ``record`` in place, even if cancelled midway."""
    record.t_start = now()
    saw_done = False
    try:
        async with session.post(
            endpoint.url, json=endpoint.payload(spec), headers=endpoint.headers
        ) as resp:
            record.http_status = resp.status
            if resp.status != 200:
                body = await resp.text(errors="replace")
                record.error = f"HTTP {resp.status}: {body.strip()}"[:_ERROR_MAX_CHARS]
                return
            parser = SSEParser()
            async for chunk in resp.content.iter_any():
                t = now()  # every event in this read arrived together
                for data in parser.feed(chunk):
                    saw_done |= _apply_event(endpoint.api, data, t, record)
            for data in parser.flush():
                saw_done |= _apply_event(endpoint.api, data, now(), record)
        if record.error is None:
            if not record.chunk_times:
                record.error = "stream contained no generated text"
            elif not saw_done and record.finish_reason is None:
                record.error = "stream ended without finish_reason or [DONE]"
    except asyncio.CancelledError:
        record.cancelled = True
        raise
    except Exception as exc:  # recorded, not raised: keep the sweep running
        record.error = f"{type(exc).__name__}: {exc}"[:_ERROR_MAX_CHARS]
    finally:
        record.t_end = now()


def _apply_event(api: str, data: str, t: float, record: RequestRecord) -> bool:
    """Apply one SSE payload to ``record``; return True if it terminates the stream."""
    if data.strip() == "[DONE]":
        return True
    obj = json.loads(data)
    if not isinstance(obj, dict):
        return False
    if obj.get("error") or obj.get("object") == "error":
        detail = obj.get("error") or obj.get("message")
        record.error = f"server error: {detail}"[:_ERROR_MAX_CHARS]
        return True
    usage = obj.get("usage")
    if isinstance(usage, dict):
        record.prompt_tokens = usage.get("prompt_tokens", record.prompt_tokens)
        record.completion_tokens = usage.get("completion_tokens", record.completion_tokens)
    for choice in obj.get("choices") or ():
        if choice.get("index", 0) != 0:
            continue
        if _choice_text(api, choice):
            if record.t_first is None:
                record.t_first = t
            record.chunk_times.append(t)
        if choice.get("finish_reason"):
            record.finish_reason = choice["finish_reason"]
    return False


def _choice_text(api: str, choice: dict[str, Any]) -> str:
    if api == "chat":
        delta = choice.get("delta") or {}
        return (
            delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or ""
        )
    return choice.get("text") or ""
