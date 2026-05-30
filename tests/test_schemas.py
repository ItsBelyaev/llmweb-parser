"""
Тесты Pydantic-моделей (app/models/schemas.py).
Проверяем валидацию URL, цены, изображения.
"""

import pytest
from pydantic import ValidationError

from app.models.schemas import ParseRequest, ProductData, InteractionCreate


# ─── ParseRequest ─────────────────────────────────────────────────────────────


def test_parse_request_valid():
    """Корректный запрос должен проходить валидацию"""
    req = ParseRequest(url="https://www.wildberries.ru/catalog/123/detail.aspx", source="russian")
    assert req.url.startswith("https://")
    assert req.source == "russian"


def test_parse_request_legacy_source_normalised():
    """Старые значения source (wildberries и т.д.) приводятся к 'russian'."""
    for legacy in ("wildberries", "ozon", "yandex_market", "mvideo", "dns"):
        req = ParseRequest(url="https://example.com/p/1", source=legacy)
        assert req.source == "russian", (legacy, req.source)


def test_parse_request_international_source():
    """Допустимое значение 'international' проходит как есть."""
    req = ParseRequest(url="https://amazon.com/p/1", source="international")
    assert req.source == "international"


def test_parse_request_url_must_have_scheme():
    """URL без схемы должен вызывать ValidationError"""
    with pytest.raises(ValidationError) as exc_info:
        ParseRequest(url="wildberries.ru/catalog/123", source="wildberries")
    assert "http" in str(exc_info.value).lower() or "url" in str(exc_info.value).lower()


def test_parse_request_empty_url():
    """Пустой URL должен вызывать ValidationError"""
    with pytest.raises(ValidationError):
        ParseRequest(url="", source="wildberries")


def test_parse_request_url_stripped():
    """URL с пробелами должен очищаться"""
    req = ParseRequest(url="  https://example.com/product  ", source="other")
    assert req.url == "https://example.com/product"


def test_parse_request_invalid_source_becomes_auto():
    """Неизвестный источник должен стать 'auto' — на бекенде
    сработает detect_source_from_url, который сам определит регион."""
    req = ParseRequest(url="https://example.com/p", source="frobnicate")
    assert req.source == "auto"


def test_parse_request_url_too_long():
    """Слишком длинный URL должен вызывать ошибку"""
    with pytest.raises(ValidationError):
        ParseRequest(url="https://example.com/" + "x" * 2100, source="other")


# ─── ProductData ──────────────────────────────────────────────────────────────


def test_product_data_valid():
    """Корректные данные товара должны проходить валидацию"""
    p = ProductData(
        title="Смартфон Samsung",
        price=29990.0,
        article="SM-A55",
        image_url="https://images.wb.ru/product.jpg",
    )
    assert p.title == "Смартфон Samsung"
    assert p.price == 29990.0


def test_product_data_negative_price():
    """Отрицательная цена должна вызывать ValidationError"""
    with pytest.raises(ValidationError):
        ProductData(title="Товар", price=-100.0)


def test_product_data_unrealistic_high_price():
    """Цена > 10 млн должна вызывать ValidationError"""
    with pytest.raises(ValidationError):
        ProductData(title="Товар", price=15_000_000.0)


def test_product_data_price_rounded():
    """Цена должна округляться до 2 знаков"""
    p = ProductData(title="Товар", price=99.999)
    assert p.price == 100.0


def test_product_data_invalid_image_url():
    """Невалидный URL изображения должен стать None"""
    p = ProductData(title="Товар", image_url="not-a-url")
    assert p.image_url is None


def test_product_data_valid_image_url():
    """Валидный URL изображения должен сохраняться"""
    url = "https://cdn.example.com/images/product123.jpg"
    p = ProductData(title="Товар", image_url=url)
    assert p.image_url == url


def test_product_data_all_none():
    """Все поля могут быть None"""
    p = ProductData()
    assert p.title is None
    assert p.price is None
    assert p.article is None
    assert p.image_url is None


def test_product_data_title_stripped():
    """Название товара должно очищаться от пробелов по краям"""
    p = ProductData(title="  Ноутбук  ")
    assert p.title == "Ноутбук"


def test_product_data_empty_title_becomes_none():
    """Пустое название должно стать None"""
    p = ProductData(title="   ")
    assert p.title is None


# ─── InteractionCreate ────────────────────────────────────────────────────────


def test_interaction_create_valid():
    """Корректное действие проходит валидацию"""
    i = InteractionCreate(action="parse_request", payload="https://example.com")
    assert i.action == "parse_request"


def test_interaction_create_empty_action():
    """Пустое действие должно вызывать ValidationError"""
    with pytest.raises(ValidationError):
        InteractionCreate(action="")


def test_interaction_create_action_truncated():
    """Действие длиннее 100 символов должно обрезаться"""
    long_action = "a" * 200
    i = InteractionCreate(action=long_action)
    assert len(i.action) == 100
