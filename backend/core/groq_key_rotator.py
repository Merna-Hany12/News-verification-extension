"""
Rotating multi-key ASYNC Groq client wrapper.

Keeps a pool of Groq API keys and automatically moves to the next key when
the current one hits a rate limit (HTTP 429). Cooling-down keys are skipped
until their reset window has likely passed, and the pool cycles rather than
sticking to key #1 forever.

Matches AsyncGroq (not the sync Groq client) since it's meant to drop into
async graph nodes like llm_verify_node.

Usage:
    from backend.core.groq_key_rotator import GroqKeyRotator

    rotator = GroqKeyRotator([
        os.environ["GROQ_API_KEY_1"],
        os.environ["GROQ_API_KEY_2"],
        os.environ["GROQ_API_KEY_3"],
    ])

    response = await rotator.chat_completion(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "..."}],
    )
"""
import time
import asyncio
import itertools
import logging
from dataclasses import dataclass
try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

from groq import AsyncGroq, RateLimitError, APIStatusError

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_S = 60.0  # fallback if the API doesn't tell us retry_after


@dataclass
class _KeyState:
    api_key: str
    client: AsyncGroq
    blocked_until: float = 0.0  # epoch seconds; 0 = not blocked

    def is_available(self, now: float) -> bool:
        return now >= self.blocked_until


class AllKeysExhaustedError(RuntimeError):
    """Raised when every key in the pool is currently rate-limited."""


class GroqKeyRotator:
    def __init__(self, api_keys: list[str], default_cooldown_s: float = DEFAULT_COOLDOWN_S):
        if not api_keys:
            raise ValueError("GroqKeyRotator needs at least one API key")
        self._states = [_KeyState(api_key=k, client=AsyncGroq(api_key=k)) for k in api_keys]
        self._cycle = itertools.cycle(range(len(self._states)))
        self._default_cooldown_s = default_cooldown_s

    def _next_available_index(self) -> int | None:
        now = time.time()
        for _ in range(len(self._states)):
            idx = next(self._cycle)
            if self._states[idx].is_available(now):
                return idx
        return None

    def _mark_blocked(self, idx: int, retry_after: float | None) -> None:
        cooldown = retry_after if retry_after is not None else self._default_cooldown_s
        self._states[idx].blocked_until = time.time() + cooldown
        logger.warning(
            "Groq key #%d rate-limited, cooling down %.0fs", idx, cooldown
        )

    @traceable(name="groq_rotator_call", run_type="llm")
    async def chat_completion(self, max_retries: int | None = None, max_wait_s: float = 8.0, **kwargs):
        """
        Drop-in-ish replacement for `await client.chat.completions.create(**kwargs)`,
        transparently rotating across keys on rate limit.

        max_wait_s: cap on how long we'll wait for a cooling-down key to free up
        before giving up. Keeps a benchmark/production request from blocking for
        minutes when every key is exhausted — better to fail this one call fast
        and let the caller fall back, than to hang the whole pipeline.
        """
        attempts = max_retries if max_retries is not None else len(self._states)

        for attempt in range(attempts):
            idx = self._next_available_index()
            if idx is None:
                soonest = min(s.blocked_until for s in self._states)
                wait_s = max(0.0, soonest - time.time())
                if wait_s > max_wait_s:
                    raise AllKeysExhaustedError(
                        f"All Groq keys rate-limited; soonest recovery in {wait_s:.0f}s "
                        f"(> max_wait_s={max_wait_s}s), failing fast instead of blocking."
                    )
                if wait_s > 0:
                    logger.warning("All Groq keys cooling down, waiting %.1fs", wait_s)
                    await asyncio.sleep(wait_s)
                idx = self._next_available_index()
                if idx is None:
                    raise AllKeysExhaustedError(
                        "All Groq API keys are rate-limited and none have recovered."
                    )

            state = self._states[idx]
            try:
                return await state.client.chat.completions.create(**kwargs)
            except RateLimitError as e:
                retry_after = _extract_retry_after(e)
                self._mark_blocked(idx, retry_after)
                continue
            except APIStatusError:
                # Non-rate-limit API error (bad request, auth, model issue) —
                # don't burn through the whole pool for this, raise immediately.
                raise

        raise AllKeysExhaustedError(
            f"Exhausted {attempts} attempts across the key pool without success."
        )


def _extract_retry_after(err: RateLimitError) -> float | None:
    """Best-effort extraction of a Retry-After header/value from the SDK error."""
    try:
        headers = getattr(err.response, "headers", {}) or {}
        val = headers.get("retry-after")
        return float(val) if val is not None else None
    except Exception:
        return None