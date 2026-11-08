# app/parsers/ozon.py
import asyncio
import logging
import re

from app.database.models import Marketplace
from app.parsers.base import BaseParser, ParsedItem
from app.parsers.browser import (
    ParseBlockedError,
    get_page_data_async,
    parse_price_text,
    seed_profile_via_real_chrome,
)

logger = logging.getLogger(__name__)


class OzonParser(BaseParser):
    _PRICE_RE = re.compile(r"(\d[\d\s\u00A0]*?)\s*(?:₽|руб)", re.IGNORECASE)

    async def parse(self, url: str) -> ParsedItem:
        # Ищем виджет цены. Внутри него может быть несколько цен
        # (с Ozon Картой и без), поэтому берём текст всего виджета.
        selector = "div[data-widget='webPrice'], div[data-widget^='webPrice']"

        try:
            data = await get_page_data_async(url, selector, "h1")
        except ParseBlockedError:
            # Анти-бот выдал страницу-инцидент. Сеем доверие настоящим
            # Chrome: короткое окно в углу экрана, куки доверия пишутся
            # в общий профиль, после этого headless-парсинг проходит сам.
            logger.warning(
                "Ozon выдал страницу-инцидент, сеем доверие настоящим Chrome"
            )
            loop = asyncio.get_running_loop()
            seeded = await loop.run_in_executor(
                None, seed_profile_via_real_chrome, url, 75.0
            )
            if seeded:
                try:
                    data = await get_page_data_async(url, selector, "h1")
                except ParseBlockedError:
                    data = await self._parse_offscreen(url, selector)
            else:
                data = await self._parse_offscreen(url, selector)

        # 1) Цена из JSON-LD разметки (самая надёжная)
        price = data.get("jsonld_price")

        # 2) Цена из текста виджета: только числа рядом с ₽
        if price is None:
            price = parse_price_text(data["price_text"])

        if price is None:
            snippet = (data["price_text"] or data.get("html", ""))[:120]
            raise ValueError(f"Не удалось распознать цену на Ozon: {snippet!r}")

        title = data["title_text"] or "Товар Ozon"

        return ParsedItem(
            marketplace=Marketplace.OZON,
            title=title,
            price=price,
        )

    @staticmethod
    async def _parse_offscreen(url: str, selector: str) -> dict:
        """Последний шанс: невидимое окно за пределами экрана."""
        logger.warning("Ozon: пробуем off-screen окно как запасной вариант")
        return await get_page_data_async(url, selector, "h1", headless=False)
