"""CPU-only mock of an OpenAI-compatible LLM server, used to validate the load generator.

The engine imitates continuous batching. One loop runs *steps*: each step emits one token to every
running sequence and admits waiting ones (up to ``max_num_seqs``), whose first token comes out of
the step that prefills them. A step lasts::

    decode_base_ms + decode_per_seq_ms * running
        + sum(prefill_base_ms + prefill_per_token_ms * prompt_tokens)   # over admitted sequences

Prompt tokens are counted as whitespace-separated words. With known parameters the expected TTFT,
TPOT and throughput are analytic, which is what ``bench smoke`` checks the client against.

Endpoints: ``POST /v1/completions`` and ``POST /v1/chat/completions`` (streaming or not),
``GET /v1/models``, ``GET /health`` (503 until ``load_delay_s`` has passed, like a server still
loading weights) and ``GET /metrics`` (Prometheus text using vLLM's metric names).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

from aiohttp import web

from bench._words import WORDS

_FINISH = object()
_ABORT = object()


@dataclass(frozen=True)
class MockConfig:
    model: str = "mock-model"
    decode_base_ms: float = 10.0
    decode_per_seq_ms: float = 0.5
    prefill_base_ms: float = 0.0
    prefill_per_token_ms: float = 0.0
    max_num_seqs: int = 16
    jitter_ms: float = 0.0
    load_delay_s: float = 0.0
    error_rate: float = 0.0
    default_max_tokens: int = 128
    seed: int = 0
    # asyncio.sleep overshoots by ~1 ms (e.g. macOS timer coalescing), which would surface in
    # TTFT whenever a step starts from idle. The engine sleeps until this long before each step
    # deadline, then yields to the event loop until the deadline. 0 disables (saves CPU).
    spin_ms: float = 2.0

    def step_ms(self, running: int, admitted_prompt_tokens: Sequence[int] = ()) -> float:
        prefill = sum(
            self.prefill_base_ms + self.prefill_per_token_ms * p for p in admitted_prompt_tokens
        )
        return self.decode_base_ms + self.decode_per_seq_ms * running + prefill

    def to_cli_args(self) -> list[str]:
        """Flags for ``python -m bench mock`` that reproduce this config."""
        return [arg for f in fields(self) for arg in (_flag(f.name), str(getattr(self, f.name)))]


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def add_mock_arguments(parser: argparse.ArgumentParser) -> None:
    """One ``--flag`` per :class:`MockConfig` field, typed from its default."""
    for f in fields(MockConfig):
        parser.add_argument(_flag(f.name), type=type(f.default), default=f.default)


def mock_config_from_args(args: argparse.Namespace) -> MockConfig:
    return MockConfig(**{f.name: getattr(args, f.name) for f in fields(MockConfig)})


@dataclass(eq=False)
class _Sequence:
    prompt_tokens: int
    max_tokens: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    generated: int = 0
    aborted: bool = False


class Engine:
    def __init__(self, cfg: MockConfig) -> None:
        self.cfg = cfg
        self.waiting: deque[_Sequence] = deque()
        self.running: list[_Sequence] = []
        self.prompt_tokens_total = 0
        self.generation_tokens_total = 0
        self._wake = asyncio.Event()
        self._rng = random.Random(cfg.seed)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.abort_all()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def submit(self, seq: _Sequence) -> None:
        self.waiting.append(seq)
        self.prompt_tokens_total += seq.prompt_tokens
        self._wake.set()

    def abort_all(self) -> None:
        for seq in (*self.running, *self.waiting):
            seq.aborted = True
            seq.queue.put_nowait(_ABORT)
        self.running.clear()
        self.waiting.clear()

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        deadline: float | None = None
        while True:
            self.running = [s for s in self.running if not s.aborted]
            admitted = []
            while self.waiting and len(self.running) < self.cfg.max_num_seqs:
                seq = self.waiting.popleft()
                if not seq.aborted:
                    self.running.append(seq)
                    admitted.append(seq)
            if not self.running:
                self._wake.clear()
                await self._wake.wait()
                deadline = None
                continue

            step = self.cfg.step_ms(len(self.running), [s.prompt_tokens for s in admitted]) / 1e3
            if self.cfg.jitter_ms:
                step = max(0.0, step + self._rng.uniform(-1, 1) * self.cfg.jitter_ms / 1e3)
            # Schedule against absolute deadlines so timer overshoot does not accumulate; restart
            # the clock after idling or when far behind, rather than bursting to catch up.
            now = loop.time()
            if deadline is None or now - deadline > step:
                deadline = now
            deadline += step
            await self._sleep_until(loop, deadline)

            still_running = []
            for seq in self.running:
                if seq.aborted:
                    continue
                seq.queue.put_nowait(seq.generated)
                seq.generated += 1
                self.generation_tokens_total += 1
                if seq.generated >= seq.max_tokens:
                    seq.queue.put_nowait(_FINISH)
                else:
                    still_running.append(seq)
            self.running = still_running

    async def _sleep_until(self, loop: asyncio.AbstractEventLoop, deadline: float) -> None:
        spin_s = self.cfg.spin_ms / 1e3
        remaining = deadline - loop.time()
        if remaining > spin_s:
            await asyncio.sleep(remaining - spin_s)
        while loop.time() < deadline:
            await asyncio.sleep(0)  # yield, so handlers keep streaming while we wait


class _State:
    def __init__(self, cfg: MockConfig) -> None:
        self.cfg = cfg
        self.ready_at = float("inf")
        self.rng = random.Random(cfg.seed + 1)

    @property
    def ready(self) -> bool:
        return time.monotonic() >= self.ready_at


ENGINE = web.AppKey("engine", Engine)
STATE = web.AppKey("state", _State)


def build_app(cfg: MockConfig) -> web.Application:
    app = web.Application()
    app[ENGINE] = Engine(cfg)
    app[STATE] = _State(cfg)
    app.cleanup_ctx.append(_lifecycle)
    app.on_shutdown.append(_abort_all)
    app.router.add_get("/health", _health)
    app.router.add_get("/metrics", _metrics)
    app.router.add_get("/v1/models", _models)
    app.router.add_post("/v1/completions", _completions)
    app.router.add_post("/v1/chat/completions", _chat_completions)
    return app


async def _lifecycle(app: web.Application) -> AsyncIterator[None]:
    app[ENGINE].start()
    app[STATE].ready_at = time.monotonic() + app[STATE].cfg.load_delay_s
    yield
    await app[ENGINE].stop()


async def _abort_all(app: web.Application) -> None:
    app[ENGINE].abort_all()


def _error(status: int, message: str) -> web.Response:
    body = {"error": {"message": message, "type": "mock_error", "code": status}}
    return web.json_response(body, status=status)


async def _health(request: web.Request) -> web.Response:
    if not request.app[STATE].ready:
        return web.json_response({"status": "loading"}, status=503)
    return web.json_response({"status": "ok"})


async def _models(request: web.Request) -> web.Response:
    model = request.app[STATE].cfg.model
    return web.json_response(
        {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "mock"}]}
    )


async def _metrics(request: web.Request) -> web.Response:
    engine = request.app[ENGINE]
    label = f'{{model_name="{request.app[STATE].cfg.model}"}}'
    metrics = [
        ("num_requests_running", "gauge", "Requests in the running batch.", len(engine.running)),
        ("num_requests_waiting", "gauge", "Requests waiting to be admitted.", len(engine.waiting)),
        ("prompt_tokens_total", "counter", "Prompt tokens processed.", engine.prompt_tokens_total),
        (
            "generation_tokens_total",
            "counter",
            "Generation tokens produced.",
            engine.generation_tokens_total,
        ),
    ]
    lines = []
    for name, kind, help_text, value in metrics:
        lines += [
            f"# HELP vllm:{name} {help_text}",
            f"# TYPE vllm:{name} {kind}",
            f"vllm:{name}{label} {value}",
        ]
    return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")


async def _completions(request: web.Request) -> web.StreamResponse:
    return await _generate(request, chat=False)


async def _chat_completions(request: web.Request) -> web.StreamResponse:
    return await _generate(request, chat=True)


def _count_prompt_tokens(body: dict[str, Any], chat: bool) -> int:
    if chat:
        parts: list[str] = []
        for message in body.get("messages") or []:
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts += [p.get("text", "") for p in content if isinstance(p, dict)]
        text = " ".join(parts)
    else:
        prompt = body.get("prompt", "")
        text = " ".join(map(str, prompt)) if isinstance(prompt, list) else str(prompt)
    return len(text.split())


def _token_text(i: int) -> str:
    return " " + WORDS[i % len(WORDS)]


async def _generate(request: web.Request, chat: bool) -> web.StreamResponse:
    state = request.app[STATE]
    if not state.ready:
        return _error(503, "model is still loading")
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _error(400, "request body is not valid JSON")
    if state.rng.random() < state.cfg.error_rate:
        return _error(500, "injected failure (error_rate)")
    try:
        max_tokens = int(
            body.get("max_tokens")
            or body.get("max_completion_tokens")
            or state.cfg.default_max_tokens
        )
    except (TypeError, ValueError):
        return _error(400, "max_tokens must be an integer")
    if max_tokens < 1:
        return _error(400, "max_tokens must be >= 1")

    seq = _Sequence(_count_prompt_tokens(body, chat), max_tokens)
    request.app[ENGINE].submit(seq)
    meta = {
        "id": f"{'chatcmpl' if chat else 'cmpl'}-{uuid.uuid4().hex[:24]}",
        "created": int(time.time()),
        "model": body.get("model") or state.cfg.model,
    }
    if body.get("stream"):
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return await _stream(request, seq, chat, meta, include_usage)
    return await _complete(seq, chat, meta)


def _usage(seq: _Sequence) -> dict[str, int]:
    return {
        "prompt_tokens": seq.prompt_tokens,
        "completion_tokens": seq.generated,
        "total_tokens": seq.prompt_tokens + seq.generated,
    }


async def _complete(seq: _Sequence, chat: bool, meta: dict[str, Any]) -> web.Response:
    tokens = []
    try:
        while (item := await seq.queue.get()) is not _FINISH:
            if item is _ABORT:
                return _error(503, "server shutting down")
            tokens.append(_token_text(item))
    finally:
        seq.aborted = True
    text = "".join(tokens)
    if chat:
        choice = {"index": 0, "message": {"role": "assistant", "content": text}}
    else:
        choice = {"index": 0, "text": text}
    choice["finish_reason"] = "length"
    obj = "chat.completion" if chat else "text_completion"
    return web.json_response({**meta, "object": obj, "choices": [choice], "usage": _usage(seq)})


async def _stream(
    request: web.Request,
    seq: _Sequence,
    chat: bool,
    meta: dict[str, Any],
    include_usage: bool,
) -> web.StreamResponse:
    obj = "chat.completion.chunk" if chat else "text_completion"

    def event(choice: dict[str, Any] | None, usage: dict[str, int] | None = None) -> bytes:
        body: dict[str, Any] = {**meta, "object": obj, "choices": [choice] if choice else []}
        if usage is not None:
            body["usage"] = usage
        return b"data: " + json.dumps(body).encode() + b"\n\n"

    def choice(text: str | None, finish_reason: str | None = None) -> dict[str, Any]:
        if chat:
            delta = {} if text is None else {"content": text}
            return {"index": 0, "delta": delta, "finish_reason": finish_reason}
        return {"index": 0, "text": text or "", "finish_reason": finish_reason}

    resp = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
    )
    await resp.prepare(request)
    try:
        if chat:  # like vLLM: an opening chunk with the role and empty content
            role = {"index": 0, "delta": {"role": "assistant", "content": ""}}
            await resp.write(event({**role, "finish_reason": None}))
        while (item := await seq.queue.get()) is not _FINISH:
            if item is _ABORT:
                return resp
            await resp.write(event(choice(_token_text(item))))
        await resp.write(event(choice(None, "length")))
        if include_usage:
            await resp.write(event(None, usage=_usage(seq)))
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
    except ConnectionResetError:
        pass  # client went away (e.g. a cancelled cool-down request); the engine drops the seq
    finally:
        seq.aborted = True
    return resp


def serve(cfg: MockConfig, host: str, port: int, access_log: bool = False) -> None:
    def announce(_: str) -> None:
        print(f"mock server for {cfg.model!r} listening on http://{host}:{port}", flush=True)

    web.run_app(
        build_app(cfg),
        host=host,
        port=port,
        access_log=web.access_logger if access_log else None,
        print=announce,
    )
