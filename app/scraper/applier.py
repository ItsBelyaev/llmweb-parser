"""Применение готовых CSS-селекторов к HTML.

Когда селекторы для домена уже сгенерированы и сохранены, нет смысла
тратить деньги на повторный вызов LLM — мы можем выдрать данные обычным
BeautifulSoup'ом. Этот модуль делает именно это.

Также модуль умеет ВАЛИДИРОВАТЬ свежесгенерированные селекторы:
проверить, что каждый CSS-селектор действительно находит элемент на той
самой странице, по которой был сгенерирован. Если нет — селектор
сбрасывается в None, а в ``markdownComments`` добавляется заметка.

Это критично против галлюцинаций LLM, которая иногда выдумывает
class-имена.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models.schemas import ProductPageSelectors

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Валидация селекторов                                                         #
# --------------------------------------------------------------------------- #


def validate_selectors(
    raw: Dict[str, Optional[str]], html: str
) -> Tuple[ProductPageSelectors, List[str]]:
    """Проверяет каждый селектор и возвращает финальную модель + комментарии.

    Невалидные или ничего-не-нашедшие селекторы заменяются на None.
    Все такие случаи фиксируются в списке комментариев — он будет
    сохранён в ``markdownComments`` разметки.
    """
    soup = BeautifulSoup(html, "lxml")
    comments: List[str] = []
    cleaned: Dict[str, Optional[str]] = {}
    for field in ("name", "price", "currency", "pictures", "category", "article"):
        sel = (raw or {}).get(field)
        if not isinstance(sel, str) or not sel.strip():
            cleaned[field] = None
            continue
        sel = sel.strip()
        try:
            hits = soup.select(sel)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Невалидный селектор для %s: %s", field, exc)
            cleaned[field] = None
            comments.append(f"{field}: некорректный CSS-селектор '{sel}'")
            continue
        if not hits:
            cleaned[field] = None
            comments.append(f"{field}: селектор '{sel}' ничего не нашёл")
            continue
        cleaned[field] = sel
    return ProductPageSelectors(**cleaned), comments


# --------------------------------------------------------------------------- #
# Применение селекторов                                                        #
# --------------------------------------------------------------------------- #


def apply_selectors(
    html: str,
    selectors: ProductPageSelectors,
    base_url: Optional[str] = None,
) -> Dict[str, Optional[object]]:
    """Применяет селекторы к HTML и возвращает словарь товара.

    Поля:
        - title:     str | None
        - price:     float | None (нормализованная)
        - currency:  str | None
        - article:   str | None
        - image_url: str | None  (абсолютный URL)
    """
    soup = BeautifulSoup(html, "lxml")

    title = _first_text(soup, selectors.name)
    article_raw = _first_text(soup, selectors.article)
    article = _normalize_article(article_raw)
    currency = _first_text(soup, selectors.currency)
    price_raw = _first_text(soup, selectors.price)
    price = _normalize_price(price_raw)

    image_url = _first_image(soup, selectors.pictures)
    if image_url and base_url:
        image_url = _absolutize(base_url, image_url)

    return {
        "title": title,
        "price": price,
        "currency": currency,
        "article": article,
        "image_url": image_url,
    }


# --------------------------------------------------------------------------- #
# Утилиты                                                                      #
# --------------------------------------------------------------------------- #


def _first_text(soup: BeautifulSoup, selector: Optional[str]) -> Optional[str]:
    if not selector:
        return None
    try:
        el = soup.select_one(selector)
    except Exception:
        return None
    if el is None:
        return None
    return el.get_text(strip=True) or None


def _first_image(soup: BeautifulSoup, selector: Optional[str]) -> Optional[str]:
    """Извлечь URL изображения по селектору.

    Поведение:
    * если селектор указывает на ``<img>`` — берём ``src`` (или ``data-src``);
    * если на контейнер с ``<img>`` внутри — берём первый img внутри;
    * если на ``<a>`` с ``href="...jpg"`` — берём href.
    """
    if not selector:
        return None
    try:
        el = soup.select_one(selector)
    except Exception:
        return None
    if el is None:
        return None
    if el.name == "img":
        return el.get("src") or el.get("data-src") or _first_from_srcset(el.get("srcset"))
    inner_img = el.find("img")
    if inner_img:
        return inner_img.get("src") or inner_img.get("data-src")
    href = el.get("href") if el.name == "a" else None
    if href and re.search(r"\.(jpg|jpeg|png|webp|avif)(\?|$)", href, re.IGNORECASE):
        return href
    return None


def _first_from_srcset(srcset: Optional[str]) -> Optional[str]:
    if not srcset:
        return None
    parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
    return parts[0] if parts else None


def _normalize_price(raw: Optional[str]) -> Optional[float]:
    """Парсит цену из произвольной строки в float."""
    if raw is None:
        return None
    s = (
        str(raw)
        .replace(" ", "")
        .replace(" ", "")
        .replace("₽", "")
        .replace("$", "")
        .replace("€", "")
        .replace("руб.", "")
        .replace("руб", "")
        .replace(",", ".")
    )
    if s.count(".") > 1:
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail
    match = re.search(r"-?\d+\.?\d*", s)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _normalize_article(raw: Optional[str]) -> Optional[str]:
    """Нормализация артикула.

    Логика:
      * Удаляем пробелы и неразрывные пробелы по краям и **внутри** —
        многие магазины пишут артикул как «2006 4875», и пробел
        бесполезен для downstream-системы.
      * Если результат — чистая цифровая последовательность (3+ цифр),
        возвращаем её. Это распространённый случай для российских
        маркетплейсов (Wildberries, Ozon).
      * Иначе возвращаем строку с удалёнными пробелами **с сохранением
        букв и дефисов** (SKU вида ``SM-A55``, ``LEN-001``).
    """
    if raw is None:
        return None
    s = str(raw).replace(" ", "").replace(" ", "").strip()
    if not s:
        return None
    # Если в строке только цифры (после удаления пробелов) — это
    # маркетплейсный артикул. Возвращаем как есть.
    if re.fullmatch(r"\d+", s) and len(s) >= 3:
        return s
    # Иначе это alphanumeric SKU — оставляем как есть.
    return s


def _absolutize(base: str, candidate: str) -> str:
    candidate = candidate.strip().strip("'\"")
    if candidate.startswith(("http://", "https://")):
        return candidate
    if candidate.startswith("//"):
        scheme = urlparse(base).scheme or "https"
        return f"{scheme}:{candidate}"
    return urljoin(base, candidate)


def origin(url: str) -> str:
    """``https://example.com/path`` → ``https://example.com``"""
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return url
    return f"{p.scheme}://{p.netloc}"
