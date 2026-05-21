"""Тесты интерактивного API (POST /api/interact, /api/cart/add, ...).

Все вызовы Playwright и LLM замокированы — никаких реальных сетевых
запросов в тестах.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.scraper.interactor import InteractionResult, InteractionStep


# ─── Валидация запросов ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interact_rejects_invalid_url(client):
    r = await client.post(
        "/api/interact",
        json={"url": "example.com/no-scheme", "action": "add_to_cart"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_interact_rejects_unknown_action(client):
    r = await client.post(
        "/api/interact",
        json={"url": "https://ex.com/p/1", "action": "frobnicate"},
    )
    assert r.status_code == 422


# ─── Успешный сценарий с моком PageInteractor ───────────────────────────────


@pytest.mark.asyncio
async def test_interact_add_to_cart_success(client):
    """Mock PageInteractor возвращает success — endpoint сохраняет и возвращает запись."""
    fake_result = InteractionResult(
        status="success",
        url="https://shop.example.com/p/1",
        action="add_to_cart",
        selector_used="button.add-to-cart",
        selector_source="heuristic",
        selector_confidence=85.0,
        element_text="В корзину",
        page_title_before="Товар",
        page_title_after="Товар (1 в корзине)",
        duration_ms=2345,
        log=[
            InteractionStep("goto", "https://shop.example.com/p/1", 0),
            InteractionStep("click", "button.add-to-cart", 1234),
            InteractionStep("done", "title='Товар (1 в корзине)'", 2345),
        ],
    )

    with patch(
        "app.api.routes.PageInteractor.run",
        new=AsyncMock(return_value=fake_result),
    ):
        r = await client.post(
            "/api/interact",
            json={
                "url": "https://shop.example.com/p/1",
                "action": "add_to_cart",
                "use_llm_fallback": True,
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["selector_used"] == "button.add-to-cart"
    assert data["selector_source"] == "heuristic"
    assert data["selector_confidence"] == 85.0
    assert data["element_text"] == "В корзину"
    assert data["duration_ms"] == 2345
    assert len(data["log"]) == 3


@pytest.mark.asyncio
async def test_interact_not_found_returns_200_with_status(client):
    """Если элемент не найден — статус 'not_found' с http 200 (по ТЗ)."""
    fake_result = InteractionResult(
        status="not_found",
        url="https://shop.example.com/p/1",
        action="add_to_cart",
        error="Не удалось найти кликабельный элемент",
        duration_ms=5678,
    )
    with patch(
        "app.api.routes.PageInteractor.run",
        new=AsyncMock(return_value=fake_result),
    ):
        r = await client.post(
            "/api/interact",
            json={"url": "https://shop.example.com/p/1", "action": "add_to_cart"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "not_found"
    assert "не удалось" in data["error"].lower()


@pytest.mark.asyncio
async def test_interact_error_returns_200_with_status(client):
    """Любая ошибка взаимодействия возвращается как status=error (не 500)."""
    fake_result = InteractionResult(
        status="error",
        url="https://shop.example.com/p/1",
        action="add_to_cart",
        error="timeout при клике: Locator.click: Timeout 15000ms exceeded.",
        duration_ms=15234,
    )
    with patch(
        "app.api.routes.PageInteractor.run",
        new=AsyncMock(return_value=fake_result),
    ):
        r = await client.post(
            "/api/interact",
            json={"url": "https://shop.example.com/p/1", "action": "add_to_cart"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    assert "timeout" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_interact_custom_selector(client):
    """action=custom_selector кладёт явный селектор в результат."""
    fake_result = InteractionResult(
        status="success",
        url="https://shop.example.com/p/1",
        action="custom_selector",
        selector_used="#fancy-button",
        selector_source="user",
        selector_confidence=100.0,
    )
    with patch(
        "app.api.routes.PageInteractor.run",
        new=AsyncMock(return_value=fake_result),
    ):
        r = await client.post(
            "/api/interact",
            json={
                "url": "https://shop.example.com/p/1",
                "action": "custom_selector",
                "selector": "#fancy-button",
            },
        )
    assert r.status_code == 200
    assert r.json()["selector_used"] == "#fancy-button"
    assert r.json()["selector_source"] == "user"


# ─── Шорткат /api/cart/add ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cart_add_shortcut(client):
    """POST /api/cart/add просто вызывает interact(add_to_cart)."""
    fake_result = InteractionResult(
        status="success",
        url="https://shop.example.com/p/1",
        action="add_to_cart",
        selector_used="button.cart",
        selector_source="heuristic",
        selector_confidence=70.0,
    )
    with patch(
        "app.api.routes.PageInteractor.run",
        new=AsyncMock(return_value=fake_result),
    ):
        r = await client.post(
            "/api/cart/add",
            json={"url": "https://shop.example.com/p/1"},
        )
    assert r.status_code == 200
    assert r.json()["action"] == "add_to_cart"


# ─── История ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interaction_runs_persist(client):
    """После выполнения действия запись должна появиться в /api/interactions/runs."""
    fake_result = InteractionResult(
        status="success",
        url="https://persist-test.example/p/1",
        action="add_to_cart",
        selector_used="button.add",
        selector_source="heuristic",
        selector_confidence=80.0,
    )
    with patch(
        "app.api.routes.PageInteractor.run",
        new=AsyncMock(return_value=fake_result),
    ):
        post_r = await client.post(
            "/api/interact",
            json={"url": "https://persist-test.example/p/1", "action": "add_to_cart"},
        )
    record_id = post_r.json()["id"]

    list_r = await client.get("/api/interactions/runs?limit=20")
    assert list_r.status_code == 200
    ids = [x["id"] for x in list_r.json()["interactions"]]
    assert record_id in ids

    one_r = await client.get(f"/api/interactions/runs/{record_id}")
    assert one_r.status_code == 200
    assert one_r.json()["url"] == "https://persist-test.example/p/1"


@pytest.mark.asyncio
async def test_interaction_runs_not_found(client):
    r = await client.get("/api/interactions/runs/9999999")
    assert r.status_code == 404
