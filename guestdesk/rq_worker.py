from rq import Worker, Connection
from redis import Redis
import os

queues = ["default"]
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

if __name__ == "__main__":
    with Connection(Redis.from_url(redis_url)):
        Worker(queues).work()
