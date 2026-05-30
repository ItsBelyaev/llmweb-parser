"""Автоматическое определение региона интернет-магазина по URL.

Используется в POST /api/parse: пользователь может не указывать поле
``source`` (или указать ``"auto"``), и сервис сам определит, российский
ли это сайт, зарубежный или вообще не e-commerce-страница.

Логика простая:
  * Известные российские маркетплейсы (Wildberries, Ozon, Yandex.Market,
    DNS, М.Видео и т.д.) считаются ``russian`` независимо от TLD.
  * Любой хост на ``.ru``, ``.рф``, ``.su``, ``.by``, ``.kz`` считается
    ``russian`` (постсоветское пространство, преимущественно русский язык).
  * Корректно разобранный URL, не подпадающий под предыдущие правила,
    считается ``international``.
  * URL без host (file://, localhost, голый IP) — ``other``.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

Source = Literal["russian", "international", "other"]


# Хосты известных российских e-commerce и сервисов.
# Используется substring-матчинг по хосту: ``in`` (включение).
# Это покрывает поддомены вида ``static.wildberries.ru`` и зеркала
# вида ``wildberries.by``.
_RUSSIAN_BRAND_HOSTS = (
    "wildberries",
    "wildberri.es",
    "ozon",
    "yandex",
    "ya.ru",
    "market.yandex",
    "mvideo",
    "dns-shop",
    "dnsshop",
    "citilink",
    "lamoda",
    "labirint",
    "kazanexpress",
    "kaspi",
    "avito",
    "drom",
    "sbermegamarket",
    "megamarket",
    "perekrestok",
    "lenta.com",
    "vprok",
    "ulmart",
    "eldorado",
    "letoile",
    "lk.cdek",
    "ldlc",
    "leroymerlin.ru",
    "ikea.com/ru",
    "petrovich.ru",
    "regard.ru",
    "kotofoto",
)

# Российские/постсоветские TLD-зоны.
_RUSSIAN_TLDS = (".ru", ".su", ".рф", ".xn--p1ai", ".by", ".kz")


def detect_source_from_url(url: str) -> Source:
    """Возвращает один из ``russian`` / ``international`` / ``other``.

    >>> detect_source_from_url("https://www.wildberries.ru/catalog/123")
    'russian'
    >>> detect_source_from_url("https://www.amazon.com/dp/B08XYZ")
    'international'
    >>> detect_source_from_url("file:///tmp/page.html")
    'other'
    """
    if not isinstance(url, str) or not url.strip():
        return "other"

    try:
        parsed = urlparse(url.strip())
    except (ValueError, AttributeError):
        return "other"

    host = (parsed.hostname or "").lower()
    if not host:
        return "other"

    # IP-адреса и localhost считаем нерелевантными.
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return "other"
    if _looks_like_ipv4(host):
        return "other"

    # Известный российский бренд.
    for brand in _RUSSIAN_BRAND_HOSTS:
        if brand in host:
            return "russian"

    # Российский TLD.
    for tld in _RUSSIAN_TLDS:
        if host.endswith(tld):
            return "russian"

    # Корректно разобранный URL с непонятным TLD — international.
    return "international"


def _looks_like_ipv4(host: str) -> bool:
    parts = host.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
