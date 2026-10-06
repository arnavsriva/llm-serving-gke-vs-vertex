"""Per-request measurement record shared by the client, the runner and the analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import pairwise
from typing import Any


@dataclass
class RequestRecord:
    """One request. Timestamps are seconds on the run's monotonic clock (0 = start of the run).

    ``chunk_times`` holds the arrival time of every streamed chunk that carried generated text.
    """

    index: int
    concurrency: int
    phase: str  # "warmup" | "measured" | "cooldown"
    prompt_words: int
    max_tokens: int
    t_start: float | None = None
    t_first: float | None = None
    t_end: float | None = None
    chunk_times: list[float] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    http_status: int | None = None
    error: str | None = None
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and not self.cancelled
            and self.t_first is not None
            and self.t_end is not None
        )

    @property
    def output_tokens(self) -> int:
        """Server-reported completion tokens if available, else the number of text chunks."""
        if self.completion_tokens is not None:
            return self.completion_tokens
        return len(self.chunk_times)

    @property
    def token_source(self) -> str:
        return "usage" if self.completion_tokens is not None else "chunks"

    @property
    def ttft(self) -> float | None:
        if self.t_first is None or self.t_start is None:
            return None
        return self.t_first - self.t_start

    @property
    def e2e(self) -> float | None:
        if self.t_end is None or self.t_start is None:
            return None
        return self.t_end - self.t_start

    @property
    def tpot(self) -> float | None:
        """Time per output token after the first: (last chunk - first chunk) / (tokens - 1)."""
        n = self.output_tokens
        if self.t_first is None or not self.chunk_times or n < 2:
            return None
        return (self.chunk_times[-1] - self.t_first) / (n - 1)

    @property
    def itls(self) -> list[float]:
        """Gaps between consecutive text chunks (a chunk is normally exactly one token)."""
        return [b - a for a, b in pairwise(self.chunk_times)]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chunk_times"] = [round(t, 6) for t in self.chunk_times]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RequestRecord:
        return cls(**d)
