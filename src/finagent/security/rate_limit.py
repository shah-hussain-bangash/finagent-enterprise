"""Request-rate quotas per principal and per tenant (NQ-028, SPR-017; prompt §22 429 + Retry-After).

Token buckets behind one port:
* `InMemoryRateLimiter` - local/test default, per process.
* `RedisRateLimiter`    - shared across API replicas; atomic Lua script; keys always tenant-prefixed
  (prompt §2). Redis is a disposable cache: if it is unreachable the limiter degrades to the
  in-process bucket rather than denying every request (rate limiting is an availability control,
  not an authorization control - authorization never depends on it).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("finagent.ratelimit")


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_s: int = 0


@dataclass(frozen=True)
class Bucket:
    per_minute: int
    burst: int

    @property
    def per_second(self) -> float:
        return self.per_minute / 60.0


class RateLimiter(Protocol):
    def hit(self, key: str, bucket: Bucket, cost: int = 1) -> RateDecision: ...


class InMemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def hit(self, key: str, bucket: Bucket, cost: int = 1) -> RateDecision:
        now = self._clock()
        with self._lock:
            tokens, last = self._state.get(key, (float(bucket.burst), now))
            tokens = min(float(bucket.burst), tokens + (now - last) * bucket.per_second)
            if tokens >= cost:
                self._state[key] = (tokens - cost, now)
                return RateDecision(True)
            self._state[key] = (tokens, now)
            return RateDecision(False, max(1, math.ceil((cost - tokens) / bucket.per_second)))


_LUA = """
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1]) or burst
local ts = tonumber(data[2]) or now
tokens = math.min(burst, tokens + (now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = math.ceil((cost - tokens) / rate / 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(burst / rate) + 1000)
return {allowed, retry}
"""


class RedisRateLimiter:
    def __init__(self, client: Any, fallback: RateLimiter | None = None) -> None:
        self._client = client
        self._script = client.register_script(_LUA)
        self._fallback = fallback or InMemoryRateLimiter()

    def hit(self, key: str, bucket: Bucket, cost: int = 1) -> RateDecision:
        per_ms = bucket.per_second / 1000.0
        try:
            allowed, retry = self._script(keys=[f"rl:{key}"], args=[per_ms, bucket.burst, cost])
        except Exception as exc:  # redis outage: degrade to per-process quota, never fail open
            log.warning("rate_limit_backend_unavailable", extra={"error_type": type(exc).__name__})
            return self._fallback.hit(key, bucket, cost)
        return RateDecision(bool(int(allowed)), max(1, int(retry)) if not int(allowed) else 0)


def build_rate_limiter(backend: str, redis_url: str | None) -> RateLimiter:
    if backend == "redis":
        import redis  # imported lazily: the local default needs no Redis

        if redis_url is None:
            raise RuntimeError("redis rate limiter without redis_url")
        return RedisRateLimiter(redis.Redis.from_url(redis_url, socket_timeout=0.25))
    return InMemoryRateLimiter()


def principal_key(tenant_id: object, principal_id: object) -> str:
    return f"t:{tenant_id}:principal:{principal_id}"


def tenant_key(tenant_id: object) -> str:
    return f"t:{tenant_id}:tenant"
