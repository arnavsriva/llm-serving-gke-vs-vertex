"""Deterministic synthetic workload: length distributions and prompt text.

Request ``i`` is a pure function of ``(seed, i)``: every target receives byte-identical prompts,
and the measured requests of each concurrency level do not depend on timing. Every prompt starts
with a unique random tag, so no two requests share a cacheable prefix (vLLM and TGI both enable
prefix caching by default, which would otherwise flatter TTFT).
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Any

from bench._words import TOPICS, WORDS

# Bump whenever prompt generation changes, so fingerprints of old runs stop matching new ones.
WORKLOAD_VERSION = 1

_INSTRUCTION = (
    "Using the notes above as loose inspiration, write a long and detailed essay about {topic}."
)
_ARITY = {"fixed": (1,), "uniform": (2,), "normal": (2, 4)}
_SPEC_HELP = "use fixed:N, uniform:LO,HI or normal:MEAN,STD[,MIN,MAX]"


@dataclass(frozen=True)
class LengthDist:
    """Integer length distribution: ``fixed:N``, ``uniform:LO,HI`` or ``normal:MEAN,STD[,MIN,MAX]``.

    ``uniform`` is inclusive on both ends; ``normal`` is rounded and clipped to ``[MIN, MAX]``
    (default ``[1, inf)``).
    """

    kind: str
    params: tuple[float, ...]

    @classmethod
    def parse(cls, spec: str) -> LengthDist:
        kind, _, rest = spec.partition(":")
        kind = kind.strip().lower()
        if kind not in _ARITY:
            raise ValueError(f"unknown length distribution {spec!r}; {_SPEC_HELP}")
        try:
            params = tuple(float(p) for p in rest.split(",")) if rest.strip() else ()
        except ValueError:
            raise ValueError(f"non-numeric parameter in {spec!r}; {_SPEC_HELP}") from None
        if len(params) not in _ARITY[kind]:
            raise ValueError(f"wrong number of parameters in {spec!r}; {_SPEC_HELP}")
        dist = cls(kind, params)
        dist._validate(spec)
        return dist

    def _validate(self, spec: str) -> None:
        p = self.params
        valid = {
            "fixed": lambda: p[0] >= 1,
            "uniform": lambda: 1 <= p[0] <= p[1],
            "normal": lambda: p[1] >= 0 and (len(p) == 2 or 1 <= p[2] <= p[3]),
        }[self.kind]()
        if not valid:
            raise ValueError(f"invalid bounds in {spec!r}; lengths must be >= 1 and LO <= HI")

    def sample(self, rng: random.Random) -> int:
        p = self.params
        if self.kind == "fixed":
            return int(p[0])
        if self.kind == "uniform":
            return rng.randint(int(p[0]), int(p[1]))
        lo, hi = (p[2], p[3]) if len(p) == 4 else (1.0, float("inf"))
        return int(min(max(round(rng.gauss(p[0], p[1])), lo), hi))

    def __str__(self) -> str:
        return f"{self.kind}:{','.join(f'{x:g}' for x in self.params)}"


@dataclass(frozen=True)
class RequestSpec:
    index: int
    prompt: str
    prompt_words: int
    max_tokens: int


def build_prompt(rng: random.Random, n_words: int) -> tuple[str, int]:
    """Return a prompt of exactly ``n_words`` whitespace-separated words (or the template minimum).

    Layout: ``[unique-tag] Notes: <filler words> <instruction>``. The tag comes first so that the
    very first tokens differ between requests, which defeats prefix caching.
    """
    tag = f"[{rng.getrandbits(48):012x}]"
    instruction = _INSTRUCTION.format(topic=rng.choice(TOPICS)).split()
    n_filler = max(0, n_words - 2 - len(instruction))
    words = [tag, "Notes:", *rng.choices(WORDS, k=n_filler), *instruction]
    return " ".join(words), len(words)


class Workload:
    """An endless, deterministic sequence of requests."""

    def __init__(self, prompt_len: LengthDist, output_len: LengthDist, seed: int = 0) -> None:
        self.prompt_len = prompt_len
        self.output_len = output_len
        self.seed = seed

    def request(self, index: int) -> RequestSpec:
        rng = random.Random(f"{WORKLOAD_VERSION}:{self.seed}:{index}")
        n_words = self.prompt_len.sample(rng)
        max_tokens = self.output_len.sample(rng)
        prompt, prompt_words = build_prompt(rng, n_words)
        return RequestSpec(index, prompt, prompt_words, max_tokens)

    def describe(self) -> dict[str, Any]:
        return {
            "version": WORKLOAD_VERSION,
            "seed": self.seed,
            "prompt_len_words": str(self.prompt_len),
            "output_len_tokens": str(self.output_len),
        }

    def fingerprint(self) -> str:
        """Short hash identifying the workload; runs are only comparable if theirs match."""
        h = hashlib.sha256(json.dumps(self.describe(), sort_keys=True).encode())
        for i in range(16):
            spec = self.request(i)
            h.update(f"{spec.max_tokens}:{spec.prompt}".encode())
        return h.hexdigest()[:16]
