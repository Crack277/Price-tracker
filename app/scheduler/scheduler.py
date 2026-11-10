from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings

settings = get_settings()

# Хранилище задач в Redis, чтобы они не терялись при перезапуске бота
jobstores = {
    "default": RedisJobStore(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
    )
}

scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="Europe/Moscow")