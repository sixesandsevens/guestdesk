# GuestDesk
# Copyright (c) 2025 Chris Tant
# SPDX-License-Identifier: LicenseRef-GDCL-1.1
import os
import time
from redis import Redis
from redis.exceptions import RedisError

IDEMPOTENCY_TTL = int(os.getenv("IDEMPOTENCY_TTL", "600"))
_redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def seen(token: str) -> bool:
    if not token:
        return False
    key = f"idemp:{token}"
    try:
        added = _redis.setnx(key, int(time.time()))
        if added:
            _redis.expire(key, IDEMPOTENCY_TTL)
        return not added
    except RedisError:
        return False


def remember(token: str, submission_id: int) -> None:
    if not token:
        return
    try:
        _redis.setex(f"idempres:{token}", IDEMPOTENCY_TTL, int(submission_id))
    except RedisError:
        return


def fetch(token: str) -> int | None:
    if not token:
        return None
    try:
        value = _redis.get(f"idempres:{token}")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    except RedisError:
        return None
