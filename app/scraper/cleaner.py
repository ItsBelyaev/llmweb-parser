"""
Очистка и нормализация HTML перед передачей в LLM
"""

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Теги, которые удаляем полностью вместе с содержимым
_REMOVE_TAGS = {
    "script", "style", "noscript", "iframe", "svg",
    "meta", "link", "head", "footer", "nav", "aside",
    "header", "form", "button", "input", "select", "textarea",
}

# Атрибуты, которые оставляем (все остальные удаляем)
_KEEP_ATTRS = {"src", "href", "alt", "class", "data-article", "data-sku", "id"}

MAX_HTML_LENGTH = 12_000  # символов — укладываемся в контекст LLM


def clean_html(html: str, max_length: int = MAX_HTML_LENGTH) -> str:
    """
    Очищает HTML от лишних тегов и атрибутов.
    Возвращает компактный HTML не длиннее max_length символов.
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")

    # Удаляем ненужные теги
    for tag in soup(_REMOVE_TAGS):
        tag.decompose()

    # Чистим атрибуты
    for tag in soup.find_all(True):
        if tag.name == "img":
            # У img оставляем src и alt
            attrs = {k: v for k, v in tag.attrs.items() if k in {"src", "alt"}}
            tag.attrs = attrs
        else:
            attrs = {k: v for k, v in tag.attrs.items() if k in _KEEP_ATTRS}
            tag.attrs = attrs

    # Удаляем пустые теги (без текста и без img внутри), сами img не трогаем
    changed = True
    while changed:
        changed = False
        for tag in soup.find_all(True):
            if tag.name in {"html", "body", "img"}:
                continue
            has_text = bool(tag.get_text(strip=True))
            has_img = bool(tag.find("img"))
            if not has_text and not has_img:
                tag.decompose()
                changed = True

    cleaned = str(soup)

    # Удаляем лишние пробелы
    cleaned = re.sub(r"\n\s*\n+", "\n", cleaned)
    cleaned = re.sub(r"  +", " ", cleaned)

    if len(cleaned) > max_length:
        logger.warning(
            "⚠️ HTML усечён с %d до %d символов", len(cleaned), max_length
        )
        cleaned = _smart_truncate(cleaned, max_length)

    logger.info("🧹 HTML очищен: %d символов", len(cleaned))
    return cleaned


def _smart_truncate(html: str, max_length: int) -> str:
    """
    Усечение HTML с сохранением структуры:
    берём первую половину и последнюю четверть.
    """
    half = max_length // 2
    quarter = max_length // 4
    return html[:half] + "\n...[truncated]...\n" + html[-(quarter):]


def extract_product_links(html: str, base_domain: Optional[str] = None) -> list[str]:
    """
    Извлекает ссылки, похожие на карточки товаров.
    Используется для обнаружения товарных страниц на листингах.
    """
    soup = BeautifulSoup(html, "lxml")
    links = []

    product_patterns = [
        r"/catalog/\d+",
        r"/product/\d+",
        r"/item/\d+",
        r"/p/\d+",
        r"/detail",
        r"\?product_id=",
        r"/products/",
    ]

    for a in soup.find_all("a", href=True):
        href = a["href"]
        for pattern in product_patterns:
            if re.search(pattern, href, re.IGNORECASE):
                if base_domain and not href.startswith("http"):
                    href = base_domain.rstrip("/") + "/" + href.lstrip("/")
                links.append(href)
                break

    return list(dict.fromkeys(links))  # дедупликация с сохранением порядка


def is_product_page(html: str) -> bool:
    """
    Эвристическая проверка: является ли страница товарной карточкой.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True).lower()

    # Признаки товарной страницы
    has_price = bool(re.search(r"\d+\s*[₽руб]", text))
    has_add_to_cart = any(
        kw in text for kw in ["в корзину", "купить", "добавить", "заказать", "add to cart"]
    )
    has_article = bool(re.search(r"арт[.\s]|артикул|sku|item\s*#", text, re.IGNORECASE))

    return has_price or has_add_to_cart or has_article
