"""Тесты применения CSS-селекторов (app/scraper/applier.py).

Покрытие:
    * validate_selectors: фильтрация невалидных и ничего-не-нашедших селекторов;
    * apply_selectors: извлечение текста/цены/изображения по селекторам;
    * нормализация цены: пробелы, неразрывные пробелы, символ ₽, запятая;
    * нормализация артикула: удаление пробелов внутри числа;
    * absolutize: относительные URL → абсолютные.
"""

from app.models.schemas import ProductPageSelectors
from app.scraper.applier import (
    apply_selectors,
    origin,
    validate_selectors,
    _absolutize,
    _normalize_article,
    _normalize_price,
)


SAMPLE_HTML = """
<html>
<body>
  <div class="m-product">
    <h1 class="name">Электрогриль Tefal OptiGrill Elite GC750D30</h1>
    <span class="price__main-value">19 999 ₽</span>
    <span class="orig-info-num">2006 4875</span>
    <picture><img src="/products/grill.jpg" alt="grill"></picture>
    <nav class="breadcrumbs">
      <a href="/category/electronics">Электроника</a>
      <a href="/category/kitchen">Кухня</a>
    </nav>
  </div>
</body>
</html>
"""


# ─── validate_selectors ──────────────────────────────────────────────────────


def test_validate_selectors_keeps_valid():
    """Селекторы, которые что-то находят, остаются на месте."""
    raw = {
        "name": ".m-product .name",
        "price": ".price__main-value",
        "article": ".orig-info-num",
        "pictures": "picture img",
        "currency": None,
        "category": ".breadcrumbs a",
    }
    sel, comments = validate_selectors(raw, SAMPLE_HTML)
    assert sel.name == ".m-product .name"
    assert sel.price == ".price__main-value"
    assert sel.pictures == "picture img"
    assert sel.article == ".orig-info-num"
    assert sel.category == ".breadcrumbs a"
    assert sel.currency is None
    assert comments == []


def test_validate_selectors_drops_nonexistent():
    """Селектор, который ничего не находит, должен стать None + коммент."""
    raw = {"name": ".does-not-exist", "price": ".price__main-value"}
    sel, comments = validate_selectors(raw, SAMPLE_HTML)
    assert sel.name is None
    assert sel.price == ".price__main-value"
    assert any("name" in c for c in comments)


def test_validate_selectors_handles_invalid_css():
    """Невалидный CSS-селектор не должен крашить валидацию."""
    raw = {"name": "<<<not a css>>>", "price": ".price__main-value"}
    sel, comments = validate_selectors(raw, SAMPLE_HTML)
    assert sel.name is None
    assert sel.price == ".price__main-value"
    assert any("name" in c for c in comments)


def test_validate_selectors_empty_input():
    """Пустой словарь → пустая модель, без комментариев."""
    sel, comments = validate_selectors({}, SAMPLE_HTML)
    assert sel.name is None
    assert sel.price is None
    assert comments == []


# ─── apply_selectors ─────────────────────────────────────────────────────────


def test_apply_selectors_extracts_fields():
    """Применение валидных селекторов извлекает все нужные поля."""
    sel = ProductPageSelectors(
        name=".m-product .name",
        price=".price__main-value",
        article=".orig-info-num",
        pictures="picture img",
    )
    result = apply_selectors(SAMPLE_HTML, sel, base_url="https://shop.example.com/p/1")
    assert result["title"] == "Электрогриль Tefal OptiGrill Elite GC750D30"
    assert result["price"] == 19999.0
    assert result["article"] == "20064875"  # пробел нормализован
    assert result["image_url"] == "https://shop.example.com/products/grill.jpg"


def test_apply_selectors_missing_selector_returns_none():
    """Если селектор отсутствует, поле должно быть None."""
    sel = ProductPageSelectors(name=".m-product .name")  # только name
    result = apply_selectors(SAMPLE_HTML, sel)
    assert result["title"] is not None
    assert result["price"] is None
    assert result["article"] is None
    assert result["image_url"] is None


def test_apply_selectors_image_from_container():
    """Селектор на контейнер с img внутри тоже возвращает картинку."""
    sel = ProductPageSelectors(pictures="picture")
    result = apply_selectors(SAMPLE_HTML, sel, base_url="https://shop.example.com/")
    assert result["image_url"] == "https://shop.example.com/products/grill.jpg"


# ─── _normalize_price ────────────────────────────────────────────────────────


def test_normalize_price_with_ruble_sign():
    assert _normalize_price("1 999 ₽") == 1999.0


def test_normalize_price_with_comma_decimal():
    assert _normalize_price("1 999,99 ₽") == 1999.99


def test_normalize_price_with_dot_thousands():
    """1.999,99 (европейский формат) → 1999.99"""
    assert _normalize_price("1.999,99 ₽") == 1999.99


def test_normalize_price_already_numeric():
    assert _normalize_price("19999") == 19999.0


def test_normalize_price_none():
    assert _normalize_price(None) is None


def test_normalize_price_garbage_returns_none():
    assert _normalize_price("no digits here") is None


# ─── _normalize_article ──────────────────────────────────────────────────────


def test_normalize_article_strips_spaces():
    assert _normalize_article("2006 4875") == "20064875"


def test_normalize_article_keeps_short_text():
    """Короткие текстовые SKU остаются как есть."""
    assert _normalize_article("SM-A55") == "SM-A55"


def test_normalize_article_none():
    assert _normalize_article(None) is None


# ─── _absolutize / origin ────────────────────────────────────────────────────


def test_absolutize_relative_url():
    assert _absolutize("https://x.com/p/1", "/img.jpg") == "https://x.com/img.jpg"


def test_absolutize_protocol_relative():
    assert _absolutize("https://x.com/p/1", "//cdn.x.com/img.jpg") == "https://cdn.x.com/img.jpg"


def test_absolutize_already_absolute():
    assert _absolutize("https://x.com/p", "https://other.com/i.jpg") == "https://other.com/i.jpg"


def test_origin_strips_path():
    assert origin("https://shop.example.com/products/123?foo=bar") == "https://shop.example.com"
