from collections.abc import AsyncIterator
from typing import Annotated

from aiogram import Bot
from aiogram3_di import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.item_repo import ItemRepository
from app.database.repositories.user_repo import UserRepository
from app.database.session import async_session_factory
from app.services.notification_service import NotificationServiceImpl
from app.services.tracking_service import TrackingServiceImpl


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Сессия БД на время обработки одного апдейта."""
    async with async_session_factory() as session:
        yield session


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRepository:
    return UserRepository(session)


def get_item_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ItemRepository:
    return ItemRepository(session)


def get_tracking_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    item_repo: Annotated[ItemRepository, Depends(get_item_repository)],
) -> TrackingServiceImpl:
    return TrackingServiceImpl(session, user_repo, item_repo)


def get_notification_service(bot: Bot) -> NotificationServiceImpl:
    return NotificationServiceImpl(bot)