"""
Pydantic-модели для валидации входящих и исходящих данных.

Модели разделены на четыре группы:

1. ``ParseRequest`` / ``ProductData`` / ``ParseResponse`` — прямое
   извлечение полей товара через LLM.
2. ``InteractionCreate`` / ``InteractionResponse`` — журнал действий
   пользователя.
3. ``ProductPageSelectors`` / ``DomainMarkup`` — режим CSS-селекторов
   (ТЗ Parsmart). Совместимо по структуре с ``domains.toon``.
4. ``SelectorsGenerateRequest`` / ``SelectorsApplyRequest`` /
   ``DomainMarkupList`` — endpoint-схемы для CSS-режима.
"""

import re
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime


# ─── Запросы ────────────────────────────────────────────────────────────────


class ParseRequest(BaseModel):
    """Запрос на парсинг страницы товара.

    По умолчанию ``source = "other"`` — сервис универсальный и работает
    с любым e-commerce сайтом, не только с маркетплейсами из списка.
    """

    url: str
    source: str = "other"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL не может быть пустым")
        if not re.match(r"^https?://", v):
            raise ValueError("URL должен начинаться с http:// или https://")
        if len(v) > 2048:
            raise ValueError("URL слишком длинный (максимум 2048 символов)")
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        allowed = {"wildberries", "ozon", "yandex_market", "other"}
        v = v.strip().lower()
        if v not in allowed:
            return "other"
        return v


# ─── Данные о товаре ────────────────────────────────────────────────────────


class ProductData(BaseModel):
    """Структура извлечённых данных о товаре"""

    title: Optional[str] = None
    price: Optional[float] = None
    article: Optional[str] = None
    image_url: Optional[str] = None

    @field_validator("price")
    @classmethod
    def validate_price(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if v < 0:
            raise ValueError("Цена не может быть отрицательной")
        if v > 10_000_000:
            raise ValueError("Цена нереалистично высока (более 10 млн)")
        if v < 0.01 and v > 0:
            raise ValueError("Цена нереалистично низкая")
        return round(v, 2)

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not re.match(r"^https?://", v):
            return None
        # Проверяем что похоже на картинку или допустимый URL
        return v

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if len(v) > 500:
            v = v[:500]
        return v


# ─── Ответы ─────────────────────────────────────────────────────────────────


class ParseResponse(BaseModel):
    """Ответ на запрос парсинга"""

    model_config = {"from_attributes": True}

    id: int
    url: str
    source: str
    status: str  # "success" | "error"
    error: Optional[str] = None
    product: Optional[ProductData] = None
    raw_response: Optional[str] = None
    timestamp: datetime


class ParseResponseList(BaseModel):
    """Список результатов парсинга"""

    results: list[ParseResponse]
    total: int


class InteractionCreate(BaseModel):
    """Создание записи о действии пользователя"""

    action: str
    payload: Optional[str] = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Действие не может быть пустым")
        if len(v) > 100:
            v = v[:100]
        return v


class InteractionResponse(BaseModel):
    """Ответ с записью о действии"""

    model_config = {"from_attributes": True}

    id: int
    action: str
    payload: Optional[str] = None
    timestamp: datetime


class InteractionList(BaseModel):
    """Список действий пользователя"""

    interactions: list[InteractionResponse]
    total: int


class HealthResponse(BaseModel):
    """Статус сервиса"""

    status: str
    service: str
    version: str


# ─── CSS-селекторы (режим Parsmart) ─────────────────────────────────────────


class ProductPageSelectors(BaseModel):
    """CSS-селекторы для страницы товара."""

    name: Optional[str] = None
    price: Optional[str] = None
    currency: Optional[str] = None
    pictures: Optional[str] = None
    category: Optional[str] = None
    article: Optional[str] = None
    regEx: List[str] = Field(default_factory=list)

    @field_validator("name", "price", "currency", "pictures", "category", "article", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v


class CartPageSelectors(BaseModel):
    """CSS-селекторы для корзины (опционально)."""

    currency: Optional[str] = None
    multiplyItemsPrice: Optional[bool] = None
    prices: Optional[str] = None
    quantities: Optional[str] = None
    titles: Optional[str] = None
    totalPrices: Optional[str] = None
    urlTemplate: Optional[str] = None


class OrderPageSelectors(BaseModel):
    """CSS-селекторы для страницы оформления заказа (опционально)."""

    confirmationElement: Optional[str] = None
    regEx: Optional[str] = None


class DomainMarkup(BaseModel):
    """Полная разметка домена, совместимая с форматом ``domains.toon``."""

    mainPageUrl: str
    device: str = "DESKTOP"
    markdownComments: List[str] = Field(default_factory=list)
    productPage: ProductPageSelectors
    cartPage: Optional[CartPageSelectors] = None
    orderPage: Optional[OrderPageSelectors] = None

    @field_validator("device", mode="before")
    @classmethod
    def _upper_device(cls, v):
        if isinstance(v, str):
            v = v.strip().upper()
            if v not in {"DESKTOP", "MOBILE"}:
                return "DESKTOP"
        return v


class SelectorsGenerateRequest(BaseModel):
    """Запрос на генерацию селекторов для домена."""

    url: str
    device: str = "DESKTOP"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL не может быть пустым")
        if not re.match(r"^https?://", v):
            raise ValueError("URL должен начинаться с http:// или https://")
        if len(v) > 2048:
            raise ValueError("URL слишком длинный")
        return v

    @field_validator("device", mode="before")
    @classmethod
    def _upper(cls, v):
        if isinstance(v, str):
            v = v.strip().upper()
            if v not in {"DESKTOP", "MOBILE"}:
                return "DESKTOP"
        return v


class SelectorsApplyRequest(BaseModel):
    """Запрос на применение сохранённых селекторов к новому URL."""

    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL не может быть пустым")
        if not re.match(r"^https?://", v):
            raise ValueError("URL должен начинаться с http:// или https://")
        return v


class SelectorsGenerateResponse(BaseModel):
    """Ответ после генерации селекторов."""

    status: str  # "success" | "error"
    domain: Optional[DomainMarkup] = None
    error: Optional[str] = None


class DomainMarkupList(BaseModel):
    """Список всех сохранённых разметок."""

    domains: List[DomainMarkup]
    total: int

