"""
Очистка и нормализация HTML перед передачей в LLM
"""

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Теги, которые удаляем полностью вместе с содержимым.
# Поведение меняется в зависимости от режима clean_html(keep_interactive=...):
# - для парсинга цен (по умолчанию) — удаляем формы и кнопки (они шум).
# - для интерактора — формы, кнопки и input ОСТАВЛЯЕМ, потому что
#   именно их нам нужно показать LLM / эвристике.
_REMOVE_TAGS_PARSE = {
    "script", "style", "noscript", "iframe", "svg",
    "meta", "link", "head", "footer", "nav", "aside",
    "header", "form", "button", "input", "select", "textarea",
}
_REMOVE_TAGS_INTERACT = {
    "script", "style", "noscript", "iframe", "svg",
    "meta", "link", "head", "footer",
    # ВНИМАНИЕ: nav и header иногда содержат корзину, поэтому не трогаем.
    # form/button/input/select/textarea ОСТАВЛЯЕМ.
}

# Атрибуты, которые оставляем (все остальные удаляем).
_KEEP_ATTRS_PARSE = {"src", "href", "alt", "class", "data-article", "data-sku", "id"}
# Для interactor оставляем гораздо больше — на этих атрибутах основано
# распознавание кнопок (aria-label, data-action, role, type, name).
_KEEP_ATTRS_INTERACT = {
    "id", "class", "src", "href", "alt", "title",
    "aria-label", "role", "type", "name", "value",
    "data-action", "data-event", "data-testid",
    "data-product-id", "data-sku", "data-id",
    "onclick", "disabled",
}

MAX_HTML_LENGTH = 12_000  # символов — укладываемся в контекст LLM


def clean_html(
    html: str,
    max_length: int = MAX_HTML_LENGTH,
    keep_interactive: bool = False,
) -> str:
    """
    Очищает HTML от лишних тегов и атрибутов.
    Возвращает компактный HTML не длиннее max_length символов.

    Args:
        html: исходный HTML.
        max_length: максимальная длина результата.
        keep_interactive: если True, бережно сохраняет ``button``, ``input``,
            ``form``, ``a`` и их атрибуты ``aria-label``, ``data-*``,
            ``role`` — это нужно для интерактивных действий (interactor).
            По умолчанию False — режим извлечения данных, в котором
            формы и кнопки лишь шумят.
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")

    remove_tags = _REMOVE_TAGS_INTERACT if keep_interactive else _REMOVE_TAGS_PARSE
    keep_attrs = _KEEP_ATTRS_INTERACT if keep_interactive else _KEEP_ATTRS_PARSE

    # Удаляем ненужные теги
    for tag in soup(remove_tags):
        tag.decompose()

    # Чистим атрибуты
    for tag in soup.find_all(True):
        if tag.name == "img":
            # У img оставляем src и alt
            attrs = {k: v for k, v in tag.attrs.items() if k in {"src", "alt"}}
            tag.attrs = attrs
        else:
            attrs = {k: v for k, v in tag.attrs.items() if k in keep_attrs}
            tag.attrs = attrs

    # Удаляем пустые теги (без текста и без img внутри).
    # В интерактивном режиме НЕ выкидываем button/input/a/form — они могут
    # быть с текстом-через-картинку (icon-only buttons).
    interactive_safe = {"html", "body", "img", "button", "input", "a", "form", "label"}
    changed = True
    while changed:
        changed = False
        for tag in soup.find_all(True):
            if keep_interactive and tag.name in interactive_safe:
                continue
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

    logger.info("🧹 HTML очищен: %d символов (interactive=%s)", len(cleaned), keep_interactive)
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
