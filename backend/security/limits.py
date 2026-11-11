import secrets
from contextlib import asynccontextmanager

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import LockError, RedisError

from config import settings

redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
# Atomic rolling window; every caller, including Classroom workers, shares this quota.
WINDOW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2])/1000000
local window = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
  local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, math.ceil(tonumber(first[2])+window-now)}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('EXPIRE', KEYS[1], window)
return {1, 0}
"""


async def enforce_rate(key: str, limit: int, window: int):
    try:
        allowed, retry = await redis.eval(WINDOW, 1, "synapse:limit:" + key,
                                          window, limit, secrets.token_hex(16))
    except RedisError:
        raise HTTPException(503, "Rate limiter unavailable; please retry later") from None
    if not allowed:
        raise HTTPException(429, "Usage limit reached", headers={"Retry-After": str(max(1, retry))})


@asynccontextmanager
async def generation_lock(user_id):
    lock = redis.lock(f"synapse:generation:{user_id}", timeout=600, blocking_timeout=0)
    try:
        acquired = await lock.acquire()
    except RedisError:
        raise HTTPException(503, "Processing queue unavailable") from None
    if not acquired:
        raise HTTPException(409, "Another document is being processed for your account")
    try:
        yield
    finally:
        try:
            await lock.release()
        except (RedisError, LockError):
            pass  # TTL still prevents a permanently stranded lock.
