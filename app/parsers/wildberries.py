# app/parsers/wildberries.py
"""Парсер Wildberries.

Основной путь — официальный публичный API карточек (card.wb.ru):
быстро, без браузера и без анти-бот страниц. Браузер используется
только как запасной вариант, если API недоступен.
"""
import asyncio
import logging
import re
from decimal import Decimal

import aiohttp
from aiohttp_socks import ProxyConnector

from app.config import get_settings
from app.database.models import Marketplace
from app.parsers.base import BaseParser, ParsedItem
from app.parsers.browser import (
    ParseBlockedError,
    get_page_data_async,
    parse_price_text,
)

logger = logging.getLogger(__name__)


class WildberriesParser(BaseParser):
    _ARTICLE_RE = re.compile(r"/catalog/(\d+)")
    _API_URL = "https://card.wb.ru/cards/v2/detail"

    async def parse(self, url: str) -> ParsedItem:
        article = self._extract_article(url)
        if article is None:
            raise ValueError(f"Не удалось извлечь артикул из ссылки WB: {url}")

        # 1) Быстрый путь — API
        try:
            parsed = await self._parse_via_api(article, url)
            if parsed is not None:
                return parsed
        except Exception as exc:
            logger.warning("WB API не сработал (%s), пробуем браузер", exc)

        # 2) Запасной путь — браузер
        return await self._parse_via_browser(url)

    # ---------- API ----------

    def _extract_article(self, url: str) -> str | None:
        match = self._ARTICLE_RE.search(url)
        return match.group(1) if match else None

    async def _parse_via_api(self, article: str, original_url: str) -> ParsedItem | None:
        params = {
            "appType": "1",
            "curr": "rub",
            "dest": "-1257786",  # Москва — дефолтная цена
            "spp": "30",
            "nm": article,
        }
        connector = self._make_connector()
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(self._API_URL, params=params) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"WB API вернул {resp.status}")
                payload = await resp.json(content_type=None)

        products = payload.get("data", {}).get("products", [])
        if not products:
            return None

        product = products[0]
        price = self._price_from_product(product)
        if price is None:
            return None

        title = (product.get("name") or "Товар Wildberries").strip()
        return ParsedItem(marketplace=Marketplace.WB, title=title, price=price)

    @staticmethod
    def _price_from_product(product: dict) -> Decimal | None:
        # Цены в API приходят в копейках
        raw = product.get("salePriceU") or product.get("priceU")
        if not raw:
            return None
        price = Decimal(str(raw)) / 100
        if price <= 0 or price > Decimal(10_000_000):
            return None
        return price.quantize(Decimal("0.01"))

    @staticmethod
    def _make_connector() -> aiohttp.BaseConnector | None:
        proxy = get_settings().parser_proxy_url_or_none
        if proxy is None:
            return None
        return ProxyConnector.from_url(proxy)

    # ---------- Браузер (запасной путь) ----------

    # WB иногда показывает временную проверку «Подозрительная активность»
    # (страница сама предлагает подождать ~1 мин и обновиться)
    _CHALLENGE_MARKER = "подозрительная активность"
    _MAX_CHALLENGE_RETRIES = 3

    async def _parse_via_browser(self, url: str) -> ParsedItem:
        clean_url = self._clean_url(url)
        # Вёрстка WB (2026): цена в <ins data-testid="product-price-final">
        selector = (
            '[data-testid="product-price-final"], '
            "ins[class*='priceBlockFinalPrice']"
        )

        for attempt in range(1, self._MAX_CHALLENGE_RETRIES + 1):
            try:
                data = await get_page_data_async(clean_url, selector, "h1")
                break
            except ParseBlockedError as exc:
                transient = exc.marker == self._CHALLENGE_MARKER
                if not transient or attempt == self._MAX_CHALLENGE_RETRIES:
                    raise
                logger.warning(
                    "WB показывает проверку (попытка %d/%d), ждём ~65 сек и повторяем",
                    attempt, self._MAX_CHALLENGE_RETRIES,
                )
                await asyncio.sleep(65)
        else:  # pragma: no cover — цикл всегда либо break, либо raise
            raise ValueError("Не удалось загрузить страницу WB")

        price = (
            data.get("jsonld_price")
            or parse_price_text(data["price_text"])
            or self._price_from_title(data.get("page_title", ""))
        )
        if price is None:
            raise ValueError("Не удалось распознать цену на странице WB")

        title = (
            self._name_from_title(data.get("page_title", ""))
            or data["title_text"].strip()
            or "Товар Wildberries"
        )
        return ParsedItem(marketplace=Marketplace.WB, title=title, price=price)

    @staticmethod
    def _price_from_title(page_title: str) -> Decimal | None:
        """Цена из <title> вида '... 222125424 купить за 619 ₽ в Wildberries'."""
        match = re.search(r"купить за\s*([\d\s\u00A0]+)\s*₽", page_title)
        return parse_price_text(match.group(1) + " ₽") if match else None

    @staticmethod
    def _name_from_title(page_title: str) -> str | None:
        """Название из <title>: убираем артикул и 'купить за ... ₽ ...'."""
        match = re.match(
            r"^(.*?)(\d+)\s+купить за\s+[\d\s\u00A0]+\s*₽", page_title
        )
        if match is None:
            return None
        name = match.group(1).strip()
        return name or None

    @staticmethod
    def _clean_url(url: str) -> str:
        match = WildberriesParser._ARTICLE_RE.search(url)
        if match:
            return f"https://www.wildberries.ru/catalog/{match.group(1)}/detail.aspx"
        return url
