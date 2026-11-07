from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Marketplace, PriceHistory, TrackedItem


class ItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        user_id: int,
        url: str,
        marketplace: Marketplace,
        title: str,
        current_price: Decimal,
        target_price: Decimal,
    ) -> TrackedItem:
        item = TrackedItem(
            user_id=user_id,
            url=url,
            marketplace=marketplace,
            title=title,
            current_price=current_price,
            target_price=target_price,
            last_checked_at=datetime.utcnow(),
        )
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)

        self.session.add(PriceHistory(item_id=item.id, price=current_price))
        await self.session.commit()

        return item

    async def list_by_user(self, user_id: int) -> list[TrackedItem]:
        result = await self.session.execute(
            select(TrackedItem)
            .where(TrackedItem.user_id == user_id)
            .order_by(TrackedItem.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, item_id: int) -> TrackedItem | None:
        result = await self.session.execute(
            select(TrackedItem).where(TrackedItem.id == item_id)
        )
        return result.scalar_one_or_none()

    async def get_all_active(self) -> list[TrackedItem]:
        result = await self.session.execute(select(TrackedItem))
        return list(result.scalars().all())

    async def delete(self, item_id: int) -> None:
        item = await self.get_by_id(item_id)
        if item is not None:
            await self.session.delete(item)
            await self.session.commit()

    async def update_price(self, item_id: int, price: Decimal) -> None:
        item = await self.get_by_id(item_id)
        if item is None:
            return
        item.current_price = price
        item.last_checked_at = datetime.utcnow()
        self.session.add(PriceHistory(item_id=item_id, price=price))
        await self.session.commit()

    async def get_history(self, item_id: int, days: int = 30) -> list[PriceHistory]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(PriceHistory)
            .where(PriceHistory.item_id == item_id, PriceHistory.checked_at >= since)
            .order_by(PriceHistory.checked_at.asc())
        )
        return list(result.scalars().all())