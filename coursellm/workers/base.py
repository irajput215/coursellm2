import dramatiq
from dramatiq.brokers.redis import RedisBroker
import os

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_broker = RedisBroker(url=redis_url)

dramatiq.set_broker(redis_broker)
