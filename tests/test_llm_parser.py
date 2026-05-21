"""
Тесты LLM-парсера (app/llm/parser.py).
Внешние API заменены mock-объектами.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.llm.parser import _extract_json, LLMParser


# ─── _extract_json ────────────────────────────────────────────────────────────


def test_extract_json_clean():
    """Чистый JSON парсится без ошибок"""
    text = '{"title": "Ноутбук", "price": 45990, "article": "123", "image_url": "https://ex.com/img.jpg"}'
    result = _extract_json(text)
    assert result is not None
    assert result["title"] == "Ноутбук"
    assert result["price"] == 45990


def test_extract_json_with_markdown_blocks():
    """JSON в markdown-блоке должен корректно извлекаться"""
    text = '```json\n{"title": "Телефон", "price": 12000}\n```'
    result = _extract_json(text)
    assert result is not None
    assert result["title"] == "Телефон"


def test_extract_json_with_preamble():
    """JSON с пояснительным текстом до/после должен извлекаться"""
    text = 'Вот результат анализа:\n{"title": "Куртка", "price": 3500}\nНадеюсь это поможет!'
    result = _extract_json(text)
    assert result is not None
    assert result["title"] == "Куртка"


def test_extract_json_invalid_returns_none():
    """Невалидный ответ без JSON должен возвращать None"""
    text = "Извините, я не смог найти данные о товаре на этой странице."
    result = _extract_json(text)
    assert result is None


def test_extract_json_trailing_comma():
    """JSON с trailing comma должен парситься"""
    text = '{"title": "Кроссовки", "price": 5990,}'
    result = _extract_json(text)
    assert result is not None
    assert result["title"] == "Кроссовки"


def test_extract_json_null_fields():
    """JSON с null-полями должен парситься корректно"""
    text = '{"title": "Товар", "price": null, "article": null, "image_url": null}'
    result = _extract_json(text)
    assert result is not None
    assert result["price"] is None
    assert result["title"] == "Товар"


# ─── LLMParser._normalize ────────────────────────────────────────────────────


def test_normalize_standard_fields():
    """Стандартные поля нормализуются корректно"""
    parser = LLMParser.__new__(LLMParser)
    data = {"title": "  Ноутбук  ", "price": "45 990", "article": "ABC123", "image_url": "https://img.com/p.jpg"}
    result = parser._normalize(data)
    assert result["title"] == "Ноутбук"
    assert result["price"] == 45990.0
    assert result["article"] == "ABC123"


def test_normalize_price_with_ruble_sign():
    """Цена с символом рубля должна нормализоваться в float"""
    parser = LLMParser.__new__(LLMParser)
    data = {"title": "Чайник", "price": "1 990₽", "article": None, "image_url": None}
    result = parser._normalize(data)
    assert result["price"] == 1990.0


def test_normalize_alternative_field_names():
    """Альтернативные имена полей (name, sku, img) должны подхватываться"""
    parser = LLMParser.__new__(LLMParser)
    data = {"name": "Кофемашина", "cost": 25000, "sku": "SKU-789", "img": "https://img.ru/p.jpg"}
    result = parser._normalize(data)
    assert result["title"] == "Кофемашина"
    assert result["price"] == 25000.0
    assert result["article"] == "SKU-789"


def test_normalize_empty_returns_nones():
    """Пустой словарь должен вернуть словарь с None"""
    parser = LLMParser.__new__(LLMParser)
    result = parser._normalize({})
    assert result["title"] is None
    assert result["price"] is None


# ─── LLMParser.extract_product_data (с mock LangChain) ───────────────────────


@pytest.mark.asyncio
async def test_extract_uses_langchain_chain():
    """extract_product_data должен использовать LangChain chain при успехе"""
    json_str = '{"title": "Смартфон", "price": 29990, "article": "SM-001", "image_url": "https://img.ru/sm.jpg"}'

    mock_response = MagicMock()
    mock_response.content = json_str

    parser = LLMParser.__new__(LLMParser)
    parser._llm = MagicMock()

    # _chain — это уже скомпонованная цепочка; мокаем её ainvoke напрямую
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    parser._chain = mock_chain

    result = await parser.extract_product_data("<html>Смартфон 29990₽</html>", "other")

    assert result["title"] == "Смартфон"
    assert result["price"] == 29990.0
    mock_chain.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_extract_falls_back_on_langchain_error():
    """При ошибке LangChain должен использоваться fallback (прямой API-вызов)"""
    parser = LLMParser.__new__(LLMParser)
    parser._llm = MagicMock()

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=RuntimeError("LangChain connection error"))
    parser._chain = mock_chain

    fallback_response = '{"title": "Планшет", "price": 15000, "article": "TAB-X", "image_url": null}'

    with patch("app.llm.parser._direct_hf_call", return_value=fallback_response):
        result = await parser.extract_product_data("<html>Планшет</html>", "other")

    assert result["title"] == "Планшет"
    assert result["price"] == 15000.0


@pytest.mark.asyncio
async def test_extract_returns_empty_when_no_llm():
    """Если LLM недоступен и fallback тоже, возвращается пустой результат"""
    parser = LLMParser.__new__(LLMParser)
    parser._llm = None
    parser._chain = None

    with patch("app.llm.parser._direct_hf_call", return_value=None):
        result = await parser.extract_product_data("<html>test</html>", "other")

    assert result["title"] is None
    assert result["price"] is None
