"""Тесты сериализации TOON (app/toon/serializer.py).

Покрытие:
    * правильный заголовок ``domains[N]:``;
    * вложенные ключи и значения с кавычками;
    * экранирование кавычек внутри строк;
    * массивы ``markdownComments`` нумеруются с 1;
    * порядок полей в productPage стабилен (как в ТЗ Parsmart).
"""

from app.models.schemas import (
    CartPageSelectors,
    DomainMarkup,
    OrderPageSelectors,
    ProductPageSelectors,
)
from app.toon.serializer import dump_domains, loads_value


def _make_markup(**overrides):
    defaults = dict(
        mainPageUrl="http://example.com",
        device="DESKTOP",
        markdownComments=[],
        productPage=ProductPageSelectors(
            name=".product-name",
            price=".product-price",
        ),
    )
    defaults.update(overrides)
    return DomainMarkup(**defaults)


# ─── dump_domains ────────────────────────────────────────────────────────────


def test_dump_empty_list():
    """Пустой список → заголовок domains[0]:."""
    out = dump_domains([])
    assert "domains[0]:" in out


def test_dump_single_domain_has_header():
    """Один домен → domains[1]: и блок 1: внутри."""
    out = dump_domains([_make_markup()])
    assert "domains[1]:" in out
    assert "    1:" in out


def test_dump_writes_main_page_url():
    out = dump_domains([_make_markup(mainPageUrl="https://shop.io")])
    assert 'mainPageUrl: "https://shop.io"' in out


def test_dump_writes_device():
    out = dump_domains([_make_markup(device="MOBILE")])
    assert 'device: "MOBILE"' in out


def test_dump_product_page_selectors():
    """Все непустые селекторы productPage должны быть в выходе."""
    out = dump_domains([_make_markup(
        productPage=ProductPageSelectors(
            name=".name",
            price=".price",
            article=".article",
            pictures=".pictures",
        ),
    )])
    assert 'name: ".name"' in out
    assert 'price: ".price"' in out
    assert 'article: ".article"' in out
    assert 'pictures: ".pictures"' in out


def test_dump_omits_null_selectors():
    """None-селекторы не должны попадать в TOON."""
    out = dump_domains([_make_markup(
        productPage=ProductPageSelectors(name=".name"),  # всё остальное None
    )])
    assert 'name: ".name"' in out
    assert "price:" not in out
    assert "article:" not in out
    assert "pictures:" not in out


def test_dump_markdown_comments_numbered():
    """Комментарии должны быть пронумерованы начиная с 1."""
    out = dump_domains([_make_markup(markdownComments=["first", "second", "third"])])
    assert "markdownComments[3]:" in out
    assert '1: "first"' in out
    assert '2: "second"' in out
    assert '3: "third"' in out


def test_dump_escapes_double_quotes():
    """Кавычки внутри значений должны экранироваться."""
    out = dump_domains([_make_markup(markdownComments=['quote "inside" comment'])])
    assert r'\"inside\"' in out


def test_dump_cart_page():
    """Если задана cartPage, она должна сериализоваться."""
    out = dump_domains([_make_markup(
        cartPage=CartPageSelectors(
            currency=".cart-currency",
            multiplyItemsPrice=True,
            prices=".cart-price",
            urlTemplate="/checkout/",
        ),
    )])
    assert "cartPage:" in out
    assert 'currency: ".cart-currency"' in out
    assert "multiplyItemsPrice: true" in out
    assert 'urlTemplate: "/checkout/"' in out


def test_dump_order_page():
    out = dump_domains([_make_markup(
        orderPage=OrderPageSelectors(
            confirmationElement="#place_order",
            regEx="checkout/order-pay/",
        ),
    )])
    assert "orderPage:" in out
    assert 'confirmationElement: "#place_order"' in out


def test_dump_multiple_domains():
    """Несколько доменов индексируются 1:, 2:, ..."""
    a = _make_markup(mainPageUrl="https://a.com")
    b = _make_markup(mainPageUrl="https://b.com")
    out = dump_domains([a, b])
    assert "domains[2]:" in out
    assert 'mainPageUrl: "https://a.com"' in out
    assert 'mainPageUrl: "https://b.com"' in out


def test_dump_field_order_in_product_page():
    """Порядок полей в productPage должен быть стабильным:
    category, currency, name, pictures, price, article.
    """
    out = dump_domains([_make_markup(
        productPage=ProductPageSelectors(
            name=".n",
            price=".p",
            article=".a",
            pictures=".pic",
            category=".cat",
            currency=".cur",
        ),
    )])
    lines = [l.strip() for l in out.splitlines() if l.strip().endswith('"') and ":" in l]
    # Извлекаем ключи в порядке появления
    keys = []
    for l in lines:
        k = l.split(":")[0]
        if k in {"category", "currency", "name", "pictures", "price", "article"}:
            keys.append(k)
    assert keys == ["category", "currency", "name", "pictures", "price", "article"]


# ─── loads_value ─────────────────────────────────────────────────────────────


def test_loads_value_strips_quotes():
    assert loads_value('"hello"') == "hello"


def test_loads_value_unescapes_quotes():
    assert loads_value(r'"with \"quotes\""') == 'with "quotes"'


def test_loads_value_no_quotes():
    """Если значение без кавычек — возвращаем как есть."""
    assert loads_value("plain") == "plain"
