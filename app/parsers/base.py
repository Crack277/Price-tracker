from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from app.database.models import Marketplace


@dataclass(slots=True, frozen=True)
class ParsedItem:
    """Результат парсинга карточки товара."""
    marketplace: Marketplace
    title: str
    price: Decimal


class BaseParser(ABC):
    """Базовый интерфейс парсера маркетплейса."""

    @abstractmethod
    async def parse(self, url: str) -> ParsedItem:
        """Извлекает название и текущую цену товара по ссылке."""
        raise NotImplementedError