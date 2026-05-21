"""
Тесты слоя базы данных (app/database/db.py).
"""

import os
import tempfile
import pytest
import pytest_asyncio

# aiosqlite открывает новое соединение на каждый вызов,
# поэтому :memory: не работает — таблицы не видны между вызовами.
# Используем реальный временный файл.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name

from app.database.db import (
    init_db,
    save_parse_result,
    get_parse_results,
    get_parse_result_by_id,
    count_parse_results,
    save_interaction,
    get_interactions,
    count_interactions,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Переинициализируем БД перед каждым тестом"""
    await init_db()


@pytest.mark.asyncio
async def test_save_and_get_parse_result():
    """Сохранённый результат должен извлекаться по ID"""
    rec_id = await save_parse_result(
        url="https://example.com/product/1",
        source="wildberries",
        status="success",
        title="Тестовый товар",
        price=999.0,
        article="ART-1",
        image_url="https://img.ru/1.jpg",
    )
    assert rec_id > 0

    row = await get_parse_result_by_id(rec_id)
    assert row is not None
    assert row["url"] == "https://example.com/product/1"
    assert row["title"] == "Тестовый товар"
    assert row["price"] == 999.0
    assert row["article"] == "ART-1"
    assert row["status"] == "success"


@pytest.mark.asyncio
async def test_save_error_result():
    """Результат с ошибкой должен сохраняться корректно"""
    rec_id = await save_parse_result(
        url="https://example.com/broken",
        source="ozon",
        status="error",
        error="Timeout",
    )
    row = await get_parse_result_by_id(rec_id)
    assert row["status"] == "error"
    assert row["error"] == "Timeout"
    assert row["title"] is None


@pytest.mark.asyncio
async def test_get_parse_results_list():
    """Список результатов должен возвращаться в порядке убывания даты"""
    await save_parse_result(url="https://a.com/1", source="other", status="success")
    await save_parse_result(url="https://a.com/2", source="other", status="success")

    results = await get_parse_results(limit=10)
    assert len(results) >= 2
    # Первый результат — самый свежий
    assert results[0]["created_at"] >= results[1]["created_at"]


@pytest.mark.asyncio
async def test_count_parse_results():
    """Счётчик должен увеличиваться при добавлении записей"""
    before = await count_parse_results()
    await save_parse_result(url="https://count.com/1", source="other", status="success")
    after = await count_parse_results()
    assert after == before + 1


@pytest.mark.asyncio
async def test_get_result_by_nonexistent_id():
    """Несуществующий ID должен возвращать None"""
    row = await get_parse_result_by_id(999999)
    assert row is None


@pytest.mark.asyncio
async def test_save_and_get_interaction():
    """Сохранённое взаимодействие должно присутствовать в списке"""
    rec_id = await save_interaction("test_action", "test_payload")
    assert rec_id > 0

    interactions = await get_interactions(limit=10)
    actions = [i["action"] for i in interactions]
    assert "test_action" in actions


@pytest.mark.asyncio
async def test_count_interactions():
    """Счётчик взаимодействий должен увеличиваться"""
    before = await count_interactions()
    await save_interaction("click_button")
    after = await count_interactions()
    assert after == before + 1


@pytest.mark.asyncio
async def test_parse_result_with_raw_response():
    """Исходный JSON-ответ LLM должен сохраняться"""
    raw = '{"title":"Тест","price":100}'
    rec_id = await save_parse_result(
        url="https://raw.com/1",
        source="other",
        status="success",
        raw_response=raw,
    )
    row = await get_parse_result_by_id(rec_id)
    assert row["raw_response"] == raw
