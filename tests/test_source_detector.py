"""Тесты автодетектора региона по URL (app/utils/source_detector.py)."""

import pytest
from app.utils.source_detector import detect_source_from_url


# ─── Российские магазины ─────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "https://www.wildberries.ru/catalog/192222089/detail.aspx",
    "https://www.ozon.ru/product/smartfon-apple-iphone-15-1305316834/",
    "https://www.dns-shop.ru/product/abc/12345/",
    "https://www.mvideo.ru/products/smartfon-12345",
    "https://www.citilink.ru/product/abc-12345/",
    "https://market.yandex.ru/product/12345",
    "https://www.lamoda.ru/p/RTLABC/clothes-brand-product/",
    "https://avito.ru/moskva/telefony/iphone",
    "https://wildberries.by/catalog/123",   # белорусское зеркало WB
    "https://kazanexpress.ru/product/12345",
])
def test_detect_russian_known_brands(url):
    assert detect_source_from_url(url) == "russian", url


@pytest.mark.parametrize("url", [
    "https://example.ru/page",
    "https://shop.example.рф/product/123",
    "http://small-shop.su/item",
    "https://magazin.by/catalog/123",
    "https://shop.kz/product/abc",
])
def test_detect_russian_by_tld(url):
    assert detect_source_from_url(url) == "russian", url


# ─── Зарубежные ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "https://www.amazon.com/dp/B08XYZ",
    "https://www.ebay.com/itm/123456",
    "https://shop.com/product/123",
    "https://www.aliexpress.com/item/100501.html",
    "https://books.toscrape.com/catalogue/a-light_1000/index.html",
    "https://store.example.io/product/1",
])
def test_detect_international(url):
    assert detect_source_from_url(url) == "international", url


# ─── Other ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "",
    "   ",
    "not-a-url",
    "file:///tmp/page.html",
    "http://localhost:8000/page",
    "http://127.0.0.1:8765/sample.html",
    "http://192.168.1.1/admin",
])
def test_detect_other(url):
    assert detect_source_from_url(url) == "other", url


def test_detect_handles_invalid_input():
    """Невалидные входные данные не должны падать."""
    assert detect_source_from_url(None) == "other"  # type: ignore[arg-type]
    assert detect_source_from_url(123) == "other"  # type: ignore[arg-type]
