import os
from redis import Redis
from rq import Queue

_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
q = Queue(connection=Redis.from_url(_redis_url))
