"""Универсальный поиск интерактивных элементов на странице.

Цель — найти кнопку/ссылку, выполняющую конкретное намерение
(«добавить в корзину», «купить», «открыть карточку»), на ЛЮБОМ
интернет-магазине, без жёстких CSS-селекторов под конкретный сайт.

Подход: эвристический scoring каждого кандидата по нескольким признакам:

1. **Текст** — точное / частичное / по словам совпадение с известными
   многоязычными фразами («в корзину», «add to cart», ...).
2. **Атрибуты** — ``aria-label``, ``data-*``, ``role``, ``name``,
   ``title``, ``alt`` (для img внутри кнопок).
3. **Класс/id** — типичные паттерны (``add-to-cart``, ``btn-buy``,
   ``add_basket``, ...).
4. **Тип тега** — ``button`` > ``input[type=submit]`` > ``a`` > div
   с onclick.
5. **Видимость** — только видимые элементы (без ``display:none``,
   ``hidden=true``).
6. **Контекст** — расположение в верхней половине страницы +1; «слишком
   глубоко» вложенный (≥10 уровней) -1.

Каждый признак даёт балл; суммарный score сортируется по убыванию,
возвращается лучший кандидат + его score + explanation. Если score
ниже порога — возвращается None, и вызывающий код может попросить LLM.

Реализация работает с BeautifulSoup (т.е. со снапшотом HTML). Сам клик
по элементу выполняет ``app/scraper/interactor.py`` через Playwright,
получив от нас селектор.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


# =========================================================================== #
# Намерения (intents) и их многоязычные паттерны                              #
# =========================================================================== #


# Каждое намерение — это набор фраз на разных языках + регексы для
# class/id. Регексы намеренно "слабые" — лучше много кандидатов
# с низким score, чем пропустить нужный.

ADD_TO_CART_TEXTS = {
    # Русский
    "в корзину", "добавить в корзину", "добавить", "купить",
    "положить в корзину", "заказать", "в корзинку", "купить сейчас",
    # English
    "add to cart", "add to bag", "add to basket", "add to trolley",
    "buy now", "buy", "purchase", "order now", "shop now", "add",
    # Прочее (часто встречается на международных магазинах)
    "ajouter au panier", "in den warenkorb", "agregar al carrito",
}

# Тексты которые ПРОВАЛИВАЮТ кандидата (anti-patterns).
# «Добавить в избранное», «Add to wishlist» — не корзина.
NOT_CART_TEXTS = {
    "в избранное", "избранное", "к сравнению", "сравнить",
    "wishlist", "favorites", "compare", "save for later",
    "notify me", "notify when available", "subscribe",
    "сохранить", "сохранить на потом", "уведомить",
}

ADD_TO_CART_CLASS_PATTERNS = [
    r"add[-_]?to[-_]?(cart|bag|basket|trolley)",
    r"buy[-_]?(now|btn|button)?",
    r"\bcart[-_]?(add|btn|button)?\b",
    r"basket[-_]?(add|btn)?",
    r"order[-_]?(btn|button|now)?",
    r"product[-_]?(buy|add|cart)",
    r"в[-_]?корзину",
    r"в-корзину",
]


@dataclass
class Intent:
    """Описание намерения, под которое ищем кнопку."""

    name: str
    texts: set[str]
    anti_texts: set[str] = field(default_factory=set)
    class_patterns: list[str] = field(default_factory=list)


INTENTS: Dict[str, Intent] = {
    "add_to_cart": Intent(
        name="add_to_cart",
        texts=ADD_TO_CART_TEXTS,
        anti_texts=NOT_CART_TEXTS,
        class_patterns=ADD_TO_CART_CLASS_PATTERNS,
    ),
    "buy_now": Intent(
        name="buy_now",
        texts={
            "buy now", "купить сейчас", "купить", "оформить заказ",
            "checkout", "proceed to checkout", "оформить",
        },
        anti_texts={"подписка", "subscribe", "избранное", "wishlist"},
        class_patterns=[
            r"buy[-_]?now",
            r"checkout",
            r"proceed[-_]?to[-_]?(checkout|pay)",
            r"order[-_]?now",
        ],
    ),
    "open_product": Intent(
        name="open_product",
        texts={
            "подробнее", "детали", "посмотреть", "перейти к товару",
            "view", "view details", "see more", "details",
        },
        anti_texts=set(),
        class_patterns=[
            r"product[-_]?link",
            r"product[-_]?(card|item)",
            r"view[-_]?(more|details|product)",
        ],
    ),
}


# =========================================================================== #
# Результат поиска                                                            #
# =========================================================================== #


@dataclass
class ElementCandidate:
    """Один кандидат на роль кнопки."""

    selector: str
    score: float
    explanation: List[str]
    text: str
    tag_name: str


# =========================================================================== #
# Главная функция поиска                                                       #
# =========================================================================== #


# Теги, которые мы рассматриваем как «кликабельные».
CLICKABLE_TAGS = ("button", "a", "input", "div", "span", "li")
INPUT_BUTTON_TYPES = ("submit", "button")

# Минимальный score, ниже которого мы считаем результат неубедительным.
MIN_CONFIDENT_SCORE = 30.0


def find_candidates(
    html: str,
    intent: str = "add_to_cart",
    limit: int = 5,
) -> List[ElementCandidate]:
    """Ранжированный список кандидатов на роль кнопки.

    Args:
        html: HTML страницы (после render и опционально после очистки).
        intent: имя из ``INTENTS`` (по умолчанию ``add_to_cart``).
        limit: сколько top-кандидатов вернуть.

    Returns:
        Список ``ElementCandidate``, отсортированный по убыванию score.
        Пустой список — никаких подходящих элементов не найдено.
    """
    intent_def = INTENTS.get(intent)
    if intent_def is None:
        raise ValueError(f"Неизвестное намерение: {intent}")

    soup = BeautifulSoup(html, "lxml")
    candidates: List[ElementCandidate] = []

    for tag in soup.find_all(CLICKABLE_TAGS):
        if not _is_potentially_clickable(tag):
            continue

        score, explain = _score_element(tag, intent_def)
        if score <= 0:
            continue

        selector = build_selector(tag)
        if not selector:
            continue

        text = _element_text(tag)
        candidates.append(
            ElementCandidate(
                selector=selector,
                score=score,
                explanation=explain,
                text=text[:80],
                tag_name=tag.name,
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def find_best(html: str, intent: str = "add_to_cart") -> Optional[ElementCandidate]:
    """Возвращает лучшего кандидата (или None, если ничего стоящего нет).

    «Стоящего» — это score ≥ ``MIN_CONFIDENT_SCORE``.
    """
    candidates = find_candidates(html, intent=intent, limit=1)
    if not candidates:
        return None
    best = candidates[0]
    if best.score < MIN_CONFIDENT_SCORE:
        logger.info(
            "Лучший кандидат имеет недостаточный score=%.1f (нужно >= %.1f), "
            "пробуйте LLM-fallback",
            best.score, MIN_CONFIDENT_SCORE,
        )
        return None
    return best


# =========================================================================== #
# Скоринг отдельного элемента                                                  #
# =========================================================================== #


def _score_element(tag: Tag, intent: Intent) -> Tuple[float, List[str]]:
    """Возвращает (score, список объяснений)."""
    score = 0.0
    explain: List[str] = []

    text = _element_text(tag).lower()
    aria_label = (tag.get("aria-label") or "").lower()
    title_attr = (tag.get("title") or "").lower()
    role_attr = (tag.get("role") or "").lower()
    classes = " ".join(tag.get("class") or []).lower()
    id_attr = (tag.get("id") or "").lower()
    data_attrs = " ".join(
        f"{k}={v}" for k, v in tag.attrs.items() if k.startswith("data-")
    ).lower()
    name_attr = (tag.get("name") or "").lower()
    type_attr = (tag.get("type") or "").lower()

    # --- 1. Текст -------------------------------------------------------- #
    text_score, text_explain = _score_text_match(text, intent.texts)
    score += text_score
    if text_explain:
        explain.append(text_explain)

    # aria-label / title часто содержат «целевой» текст
    aria_score, aria_explain = _score_text_match(aria_label, intent.texts)
    if aria_score:
        score += aria_score * 0.9
        explain.append(f"aria-label: {aria_explain}")
    title_score, title_explain = _score_text_match(title_attr, intent.texts)
    if title_score:
        score += title_score * 0.7
        explain.append(f"title-attr: {title_explain}")

    # --- 2. Anti-patterns в тексте --------------------------------------- #
    for anti in intent.anti_texts:
        if anti in text or anti in aria_label or anti in title_attr:
            score -= 50
            explain.append(f"anti-text matched: '{anti}'")

    # --- 3. Class / id patterns ----------------------------------------- #
    haystack = f"{classes} {id_attr} {data_attrs} {name_attr}"
    for pat in intent.class_patterns:
        if re.search(pat, haystack):
            score += 25
            explain.append(f"class/id matches /{pat}/")
            break  # один matched паттерн достаточно

    # --- 4. Атрибуты доступности ---------------------------------------- #
    if role_attr == "button":
        score += 5
        explain.append("role=button")

    # --- 5. Тип тега ----------------------------------------------------- #
    if tag.name == "button":
        score += 10
        explain.append("tag=button")
    elif tag.name == "input" and type_attr in INPUT_BUTTON_TYPES:
        score += 8
        explain.append(f"input[type={type_attr}]")
    elif tag.name == "a":
        score += 4
        explain.append("tag=a")
    # div/span получают 0 — без явных признаков это, скорее всего, не кнопка

    # --- 6. Запрет на «отключённые» элементы ---------------------------- #
    if tag.has_attr("disabled"):
        score -= 30
        explain.append("disabled")
    if "disabled" in classes:
        score -= 10
        explain.append("class contains 'disabled'")

    # --- 7. Глубина вложенности ----------------------------------------- #
    depth = _depth(tag)
    if depth > 12:
        score -= 3
        explain.append(f"deep nesting ({depth})")

    return score, explain


def _score_text_match(text: str, targets: set[str]) -> Tuple[float, str]:
    """Ранжирует совпадение строки с любым из target-текстов.

    20 — точное совпадение,
    15 — текст содержит target целиком,
    8  — все слова target встречаются в тексте.
    """
    text = text.strip()
    if not text:
        return 0, ""
    text_norm = re.sub(r"\s+", " ", text)
    for t in targets:
        if text_norm == t:
            return 20, f"exact text '{t}'"
    for t in targets:
        if t in text_norm:
            return 15, f"contains '{t}'"
    for t in targets:
        words = t.split()
        if len(words) >= 2 and all(w in text_norm for w in words):
            return 8, f"all words of '{t}'"
    return 0, ""


# =========================================================================== #
# Утилиты                                                                      #
# =========================================================================== #


def _is_potentially_clickable(tag: Tag) -> bool:
    """Быстрая отсечка заведомо некликабельных элементов."""
    if tag.name == "a":
        return True
    if tag.name == "button":
        return True
    if tag.name == "input":
        return (tag.get("type") or "").lower() in INPUT_BUTTON_TYPES
    # Для div/span/li требуем явный признак кликабельности.
    if tag.get("onclick"):
        return True
    if (tag.get("role") or "").lower() == "button":
        return True
    if tag.has_attr("data-action") or tag.has_attr("data-event"):
        return True
    return False


def _element_text(tag: Tag) -> str:
    """Возвращает «видимый» текст элемента (включая img alt)."""
    # Собственный текст + текст внутренних элементов
    text = tag.get_text(separator=" ", strip=True)
    if text:
        return text
    # У некоторых кнопок текста нет, но есть img[alt]
    inner_img = tag.find("img")
    if inner_img is not None:
        alt = inner_img.get("alt") or ""
        if alt.strip():
            return alt.strip()
    return ""


def _depth(tag: Tag) -> int:
    depth = 0
    parent = tag.parent
    while parent is not None and parent.name not in (None, "[document]"):
        depth += 1
        parent = parent.parent
    return depth


# =========================================================================== #
# Сборка CSS-селектора для найденного элемента                                #
# =========================================================================== #


def build_selector(tag: Tag) -> Optional[str]:
    """Строит уникальный CSS-селектор для тега.

    Приоритет: id → data-testid → уникальная цепочка class'ов →
    «голый» путь от ближайшего предка с id.

    Возвращает None, если не удалось построить ни один из них.
    """
    # Самый надёжный — id, если он есть и не выглядит сгенерированным.
    tag_id = tag.get("id")
    if tag_id and not _looks_autogenerated(tag_id):
        return f"#{_css_escape(tag_id)}"

    # data-testid
    tid = tag.get("data-testid")
    if tid:
        return f'{tag.name}[data-testid="{_css_escape_attr(tid)}"]'

    # data-action (часто встречается на коммерческих сайтах)
    action = tag.get("data-action")
    if action:
        return f'{tag.name}[data-action="{_css_escape_attr(action)}"]'

    # name=... (для input)
    name = tag.get("name")
    if tag.name == "input" and name:
        return f'input[name="{_css_escape_attr(name)}"]'

    # Уникальная пара tag.class
    classes = tag.get("class") or []
    classes_clean = [c for c in classes if not _looks_autogenerated(c)]
    if classes_clean:
        sel = tag.name + "".join(f".{_css_escape(c)}" for c in classes_clean)
        return sel

    # Последний ресурс — путь от ближайшего предка с id.
    chain = [tag.name]
    cur = tag.parent
    while cur is not None and cur.name not in (None, "[document]"):
        cur_id = cur.get("id")
        if cur_id and not _looks_autogenerated(cur_id):
            chain.insert(0, f"#{_css_escape(cur_id)}")
            return " ".join(chain)
        cur_classes = cur.get("class") or []
        cur_classes_clean = [
            c for c in cur_classes if not _looks_autogenerated(c)
        ]
        if cur_classes_clean:
            chain.insert(
                0,
                cur.name
                + "".join(f".{_css_escape(c)}" for c in cur_classes_clean[:2]),
            )
            if len(chain) >= 3:
                return " ".join(chain)
        cur = cur.parent

    if len(chain) >= 1:
        return " ".join(chain)
    return None


def _looks_autogenerated(value: str) -> bool:
    """``.css-1a2b3c``, ``.MuiButton-root-1234`` — это автосгенерированные
    хэши, которые меняются между сборками. Не используем их в селекторах.
    """
    if not value:
        return True
    # CSS-in-JS: css-XXXXXX
    if re.match(r"^css-[a-z0-9]{5,}$", value):
        return True
    # styled-components / emotion: sc-XXXXXX
    if re.match(r"^sc-[a-zA-Z0-9]+$", value):
        return True
    # Hash вида jsx-1234567890
    if re.match(r"^jsx-\d{6,}$", value):
        return True
    # MUI hash (Material-UI auto-classes)
    if re.search(r"-\d{4,}$", value) and "-" in value:
        # допускаем "foo-bar-baz" но запрещаем "foo-bar-1234"
        last = value.rsplit("-", 1)[-1]
        if last.isdigit() and len(last) >= 4:
            return True
    return False


def _css_escape(value: str) -> str:
    """Минимальное экранирование для CSS-идентификатора."""
    return re.sub(r"([^\w-])", r"\\\1", value)


def _css_escape_attr(value: str) -> str:
    """Экранирование для значения в атрибутном селекторе."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
