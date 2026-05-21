"""
Тесты модуля очистки HTML (app/scraper/cleaner.py)
"""

import pytest
from app.scraper.cleaner import clean_html, extract_product_links, is_product_page


# ─── clean_html ──────────────────────────────────────────────────────────────


def test_clean_html_removes_scripts():
    """Script-теги должны быть удалены"""
    html = "<html><body><p>Товар</p><script>alert(1)</script></body></html>"
    result = clean_html(html)
    assert "<script>" not in result
    assert "Товар" in result


def test_clean_html_removes_style():
    """Style-теги должны быть удалены"""
    html = "<html><body><p>Цена</p><style>.x{color:red}</style></body></html>"
    result = clean_html(html)
    assert "<style>" not in result
    assert "Цена" in result


def test_clean_html_keeps_img_src():
    """Атрибут src у img должен сохраняться"""
    html = '<html><body><img src="https://example.com/product.jpg" data-id="123"/></body></html>'
    result = clean_html(html)
    assert "https://example.com/product.jpg" in result


def test_clean_html_removes_empty_divs():
    """Пустые div без текста и изображений должны удаляться"""
    html = "<html><body><div><div></div></div><p>Текст</p></body></html>"
    result = clean_html(html)
    assert "Текст" in result


def test_clean_html_truncates_long_html():
    """HTML длиннее max_length должен быть усечён"""
    long_html = "<html><body>" + "<p>x</p>" * 5000 + "</body></html>"
    result = clean_html(long_html, max_length=500)
    assert len(result) <= 700  # с запасом на truncation-маркер


def test_clean_html_empty_input():
    """Пустая строка должна вернуть пустую строку"""
    assert clean_html("") == ""


def test_clean_html_removes_nav():
    """Nav-теги (навигация) должны удаляться"""
    html = "<html><body><nav><a>Меню</a></nav><main><p>Товар</p></main></body></html>"
    result = clean_html(html)
    assert "<nav>" not in result


# ─── extract_product_links ────────────────────────────────────────────────────


def test_extract_product_links_wildberries():
    """Должны извлекаться ссылки на товары WB"""
    html = """
    <html><body>
      <a href="/catalog/123456789/detail.aspx">Товар 1</a>
      <a href="/catalog/987654321/detail.aspx">Товар 2</a>
      <a href="/about">О нас</a>
    </body></html>
    """
    links = extract_product_links(html)
    assert len(links) == 2
    assert all("/catalog/" in l for l in links)


def test_extract_product_links_deduplication():
    """Дублирующиеся ссылки не должны повторяться"""
    html = """
    <html><body>
      <a href="/product/100">Товар</a>
      <a href="/product/100">Тот же товар</a>
    </body></html>
    """
    links = extract_product_links(html)
    assert len(links) == 1


def test_extract_product_links_empty():
    """Без товарных ссылок должен вернуться пустой список"""
    html = "<html><body><a href='/about'>О нас</a></body></html>"
    links = extract_product_links(html)
    assert links == []


# ─── is_product_page ─────────────────────────────────────────────────────────


def test_is_product_page_with_price():
    """Страница с ценой должна определяться как товарная"""
    html = "<html><body><h1>Ноутбук</h1><span>45 990 ₽</span></body></html>"
    assert is_product_page(html) is True


def test_is_product_page_with_add_to_cart():
    """Страница с кнопкой 'в корзину' — товарная"""
    html = "<html><body><h1>Наушники</h1><button>В корзину</button></body></html>"
    assert is_product_page(html) is True


def test_is_product_page_false():
    """Страница без признаков товара не является товарной"""
    html = "<html><body><h1>О компании</h1><p>Мы работаем с 2000 года</p></body></html>"
    assert is_product_page(html) is False
