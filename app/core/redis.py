from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import settings

REVIEW_SUMMARY_QUEUE = "review_summary_jobs"


def get_redis():
    # Bound outage latency and avoid automatically retrying a non-idempotent push.
    with Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
    ) as client:
        yield client
