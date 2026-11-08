from urllib.parse import urlparse

from app.database.models import Marketplace
from app.parsers.base import BaseParser


def detect_marketplace(url: str) -> Marketplace | None:
    """Определяет маркетплейс по домену ссылки."""
    host = urlparse(url).hostname
    if host is None:
        return None
    host = host.lower()

    if host == "ozon.ru" or host.endswith(".ozon.ru"):
        return Marketplace.OZON
    if host == "wildberries.ru" or host.endswith(".wildberries.ru"):
        return Marketplace.WB
    return None


def get_parser_by_url(url: str) -> BaseParser:
    """Возвращает экземпляр парсера для соответствующего маркетплейса."""
    marketplace = detect_marketplace(url)
    if marketplace is None:
        raise ValueError(f"Unsupported marketplace url: {url}")

    # Ленивые импорты, чтобы модуль registry не тянул Playwright/aiohttp
    # на этапе импорта.
    if marketplace is Marketplace.WB:
        from app.parsers.wildberries import WildberriesParser
        return WildberriesParser()
    if marketplace is Marketplace.OZON:
        from app.parsers.ozon import OzonParser
        return OzonParser()

    raise ValueError(f"Unsupported marketplace: {marketplace}")