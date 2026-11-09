from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Marketplace, TrackedItem
from app.database.repositories.item_repo import ItemRepository
from app.database.repositories.user_repo import UserRepository
from app.parsers.registry import get_parser_by_url


class TrackingService(Protocol):
    """Контракт сервиса отслеживания товаров."""

    async def add_item(
        self,
        telegram_id: int,
        username: str | None,
        url: str,
        marketplace: Marketplace,
        title: str,
        current_price: Decimal,
        target_price: Decimal,
    ) -> TrackedItem:
        ...

    async def list_items(self, telegram_id: int) -> list[TrackedItem]:
        ...

    async def delete_item(self, telegram_id: int, item_id: int) -> bool:
        ...

    async def refresh_price(self, item: TrackedItem) -> bool:
        ...


class TrackingServiceImpl:
    """Реализация сервиса отслеживания товаров."""

    def __init__(
        self,
        session: AsyncSession,
        user_repo: UserRepository,
        item_repo: ItemRepository,
    ) -> None:
        self._session = session
        self._user_repo = user_repo
        self._item_repo = item_repo

    async def add_item(
        self,
        telegram_id: int,
        username: str | None,
        url: str,
        marketplace: Marketplace,
        title: str,
        current_price: Decimal,
        target_price: Decimal,
    ) -> TrackedItem:
        user = await self._user_repo.get_or_create(telegram_id, username)
        return await self._item_repo.add(
            user_id=user.id,
            url=url,
            marketplace=marketplace,
            title=title,
            current_price=current_price,
            target_price=target_price,
        )

    async def list_items(self, telegram_id: int) -> list[TrackedItem]:
        user = await self._user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            return []
        return await self._item_repo.list_by_user(user.id)

    async def delete_item(self, telegram_id: int, item_id: int) -> bool:
        user = await self._user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            return False
        item = await self._item_repo.get_by_id(item_id)
        if item is None or item.user_id != user.id:
            return False
        await self._item_repo.delete(item_id)
        return True

    async def refresh_price(self, item: TrackedItem) -> bool:
        parser = get_parser_by_url(item.url)
        parsed = await parser.parse(item.url)
        if parsed.price == item.current_price:
            return False
        
        await self._item_repo.update_price(item.id, parsed.price)
        item.current_price = parsed.price 
        return True