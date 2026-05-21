"""
Интеграционные тесты API-эндпоинтов (app/api/routes.py).
Внешние вызовы (Playwright, LLM) заменены mock-объектами.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ─── /health ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check(client):
    """/health должен возвращать статус ok"""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "LLM Web Parser" in data["service"]


# ─── GET /api/results ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_results_empty(client):
    """История должна быть пустым списком изначально"""
    response = await client.get("/api/results")
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "total" in data
    assert isinstance(data["results"], list)


# ─── POST /api/parse (mock) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_invalid_url(client):
    """Запрос без схемы http должен вернуть 422"""
    response = await client.post("/api/parse", json={"url": "wildberries.ru/product", "source": "wildberries"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_parse_empty_url(client):
    """Пустой URL должен вернуть 422"""
    response = await client.post("/api/parse", json={"url": "", "source": "wildberries"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_parse_success_with_mocks(client):
    """Успешный парсинг с mock Playwright и LLM"""
    mock_html = """
    <html><body>
      <h1>Ноутбук Lenovo</h1>
      <span class="price">55 990 ₽</span>
      <img src="https://img.wb.ru/laptop.jpg"/>
    </body></html>
    """

    mock_llm_data = {
        "title": "Ноутбук Lenovo",
        "price": 55990.0,
        "article": "LEN-001",
        "image_url": "https://img.wb.ru/laptop.jpg",
        "_raw": '{"title":"Ноутбук Lenovo","price":55990,"article":"LEN-001","image_url":"https://img.wb.ru/laptop.jpg"}',
    }

    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=mock_html)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch("app.api.routes._llm_parser.extract_product_data", new=AsyncMock(return_value=mock_llm_data)),
    ):
        response = await client.post(
            "/api/parse",
            json={"url": "https://www.wildberries.ru/catalog/999/detail.aspx", "source": "wildberries"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["product"]["title"] == "Ноутбук Lenovo"
    assert data["product"]["price"] == 55990.0
    assert data["id"] > 0


@pytest.mark.asyncio
async def test_parse_fetch_error_returns_error_status(client):
    """При ошибке загрузки страницы статус должен быть 'error'"""
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(side_effect=Exception("Timeout"))),
    ):
        response = await client.post(
            "/api/parse",
            json={"url": "https://www.wildberries.ru/catalog/000/detail.aspx", "source": "wildberries"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "Timeout" in data["error"]


@pytest.mark.asyncio
async def test_parse_result_saved_to_db(client):
    """После парсинга запись должна появиться в истории"""
    mock_html = "<html><body><h1>Товар</h1><span>100 ₽</span></body></html>"
    mock_data = {"title": "Товар", "price": 100.0, "article": None, "image_url": None, "_raw": None}

    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=mock_html)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch("app.api.routes._llm_parser.extract_product_data", new=AsyncMock(return_value=mock_data)),
    ):
        parse_resp = await client.post(
            "/api/parse",
            json={"url": "https://example.com/product/42", "source": "other"},
        )

    record_id = parse_resp.json()["id"]
    get_resp = await client.get(f"/api/results/{record_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == record_id


# ─── GET /api/results/{id} ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_result_not_found(client):
    """Несуществующий ID должен возвращать 404"""
    response = await client.get("/api/results/999999")
    assert response.status_code == 404


# ─── POST /api/interactions ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_interaction(client):
    """Запись взаимодействия должна сохраняться и возвращать ID"""
    response = await client.post(
        "/api/interactions",
        json={"action": "test_click", "payload": "button#parse"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "test_click"
    assert data["id"] > 0


@pytest.mark.asyncio
async def test_create_interaction_empty_action(client):
    """Пустое действие должно вернуть 422"""
    response = await client.post("/api/interactions", json={"action": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_interactions(client):
    """GET /api/interactions должен возвращать список"""
    await client.post("/api/interactions", json={"action": "test_list"})
    response = await client.get("/api/interactions")
    assert response.status_code == 200
    data = response.json()
    assert "interactions" in data
    assert data["total"] >= 1


# ─── DELETE /api/results ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_results(client):
    """После очистки история должна быть пустой"""
    await client.delete("/api/results")
    response = await client.get("/api/results")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
