"""Тесты CSS-роутов API (app/api/routes.py: /selectors/*).

Покрытие:
    * POST /api/selectors/generate с моками Playwright и LLM;
    * POST /api/selectors/apply (использует ранее сохранённую разметку);
    * GET /api/selectors — список;
    * GET /api/selectors/{domain} — получение конкретной разметки;
    * DELETE /api/selectors/{domain};
    * GET /api/selectors/export.toon — выгрузка в TOON-формат.

Внешние вызовы (Playwright fetch_page и LLM) подменены через
``unittest.mock`` — никаких реальных сетевых запросов в тестах.
"""

import pytest
from unittest.mock import AsyncMock, patch


SAMPLE_HTML = """
<html>
<body>
  <div class="m-product">
    <h1 class="product-name">Тестовый товар</h1>
    <span class="product-price">1 990 ₽</span>
    <span class="article">SKU-001</span>
    <img class="main-img" src="https://cdn.example.com/test.jpg" />
  </div>
</body>
</html>
"""


# ─── POST /api/selectors/generate ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_selectors_generate_invalid_url(client):
    """URL без схемы должен дать 422."""
    r = await client.post(
        "/api/selectors/generate",
        json={"url": "example.com/no-scheme", "device": "DESKTOP"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_selectors_generate_success(client):
    """Успешная генерация сохраняет разметку и возвращает её."""
    mock_selectors = {
        "name": ".product-name",
        "price": ".product-price",
        "currency": None,
        "pictures": ".main-img",
        "category": None,
        "article": ".article",
        "_raw": "{...}",
    }
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=SAMPLE_HTML)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch(
            "app.api.routes._llm_parser.generate_selectors",
            new=AsyncMock(return_value=mock_selectors),
        ),
    ):
        r = await client.post(
            "/api/selectors/generate",
            json={"url": "https://shop.example.com/p/123", "device": "DESKTOP"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    d = data["domain"]
    assert d["mainPageUrl"] == "https://shop.example.com"
    assert d["productPage"]["name"] == ".product-name"
    assert d["productPage"]["price"] == ".product-price"
    assert d["productPage"]["pictures"] == ".main-img"
    assert d["productPage"]["article"] == ".article"


@pytest.mark.asyncio
async def test_selectors_generate_filters_garbage(client):
    """Селектор, ничего не нашедший на странице, должен быть отброшен."""
    mock_selectors = {
        "name": ".this-class-does-not-exist",
        "price": ".product-price",
    }
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=SAMPLE_HTML)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch(
            "app.api.routes._llm_parser.generate_selectors",
            new=AsyncMock(return_value=mock_selectors),
        ),
    ):
        r = await client.post(
            "/api/selectors/generate",
            json={"url": "https://garbage-test.example/p/1", "device": "DESKTOP"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["domain"]["productPage"]["name"] is None
    assert data["domain"]["productPage"]["price"] == ".product-price"
    assert len(data["domain"]["markdownComments"]) >= 1


@pytest.mark.asyncio
async def test_selectors_generate_fetch_failure(client):
    """При ошибке загрузки страницы возвращается status=error."""
    with patch(
        "app.api.routes.fetch_page",
        new=AsyncMock(side_effect=RuntimeError("Connection refused")),
    ):
        r = await client.post(
            "/api/selectors/generate",
            json={"url": "https://unreachable.example/p/1", "device": "DESKTOP"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"
    assert "Connection refused" in data["error"]


# ─── POST /api/selectors/apply ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_selectors_apply_uses_saved_markup(client):
    """Apply должен переиспользовать сохранённую разметку без вызова LLM."""
    # Сначала генерируем
    mock_selectors = {
        "name": ".product-name",
        "price": ".product-price",
        "article": ".article",
        "pictures": ".main-img",
    }
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=SAMPLE_HTML)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch(
            "app.api.routes._llm_parser.generate_selectors",
            new=AsyncMock(return_value=mock_selectors),
        ),
    ):
        await client.post(
            "/api/selectors/generate",
            json={"url": "https://apply-test.example/p/1", "device": "DESKTOP"},
        )

    # Теперь apply — LLM НЕ должен вызываться.
    llm_spy = AsyncMock()
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=SAMPLE_HTML)),
        patch("app.api.routes._llm_parser.generate_selectors", new=llm_spy),
        patch(
            "app.api.routes._llm_parser.extract_product_data",
            new=llm_spy,
        ),
    ):
        r = await client.post(
            "/api/selectors/apply",
            json={"url": "https://apply-test.example/p/2"},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["product"]["title"] == "Тестовый товар"
    assert data["product"]["price"] == 1990.0
    assert data["product"]["article"] == "SKU-001"
    assert "cdn.example.com" in data["product"]["image_url"]
    llm_spy.assert_not_called()  # ← главная проверка: LLM не вызывался


@pytest.mark.asyncio
async def test_selectors_apply_no_markup_returns_404(client):
    """Если для домена нет разметки — 404."""
    r = await client.post(
        "/api/selectors/apply",
        json={"url": "https://nonexistent-domain-xyz.example/p/1"},
    )
    assert r.status_code == 404


# ─── GET /api/selectors ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_selectors_list_returns_saved(client):
    """GET /selectors должен возвращать все сохранённые разметки."""
    # Сохраняем хотя бы одну
    mock_selectors = {"name": ".product-name", "price": ".product-price"}
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=SAMPLE_HTML)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch(
            "app.api.routes._llm_parser.generate_selectors",
            new=AsyncMock(return_value=mock_selectors),
        ),
    ):
        await client.post(
            "/api/selectors/generate",
            json={"url": "https://list-test.example/p/1", "device": "DESKTOP"},
        )

    r = await client.get("/api/selectors")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    urls = [d["mainPageUrl"] for d in data["domains"]]
    assert "https://list-test.example" in urls


# ─── GET /api/selectors/{domain} ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_selectors_get_by_domain(client):
    mock_selectors = {"name": ".product-name", "price": ".product-price"}
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=SAMPLE_HTML)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch(
            "app.api.routes._llm_parser.generate_selectors",
            new=AsyncMock(return_value=mock_selectors),
        ),
    ):
        await client.post(
            "/api/selectors/generate",
            json={"url": "https://get-test.example/p/1", "device": "DESKTOP"},
        )

    r = await client.get("/api/selectors/get-test.example")
    assert r.status_code == 200
    assert r.json()["mainPageUrl"] == "https://get-test.example"


@pytest.mark.asyncio
async def test_selectors_get_not_found(client):
    r = await client.get("/api/selectors/no-such-domain-xyz.example")
    assert r.status_code == 404


# ─── DELETE /api/selectors/{domain} ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_selectors_delete(client):
    mock_selectors = {"name": ".product-name", "price": ".product-price"}
    with (
        patch("app.api.routes.fetch_page", new=AsyncMock(return_value=SAMPLE_HTML)),
        patch("app.api.routes._llm_parser.is_available", return_value=True),
        patch(
            "app.api.routes._llm_parser.generate_selectors",
            new=AsyncMock(return_value=mock_selectors),
        ),
    ):
        await client.post(
            "/api/selectors/generate",
            json={"url": "https://delete-test.example/p/1", "device": "DESKTOP"},
        )

    # Удаляем
    r = await client.delete("/api/selectors/delete-test.example")
    assert r.status_code == 200

    # Повторное удаление → 404
    r = await client.delete("/api/selectors/delete-test.example")
    assert r.status_code == 404


# ─── GET /api/selectors/export.toon ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_selectors_export_toon(client):
    """Экспорт должен быть в формате TOON с заголовком domains[N]:."""
    r = await client.get("/api/selectors/export.toon")
    assert r.status_code == 200
    text = r.text
    assert text.startswith("domains[")
