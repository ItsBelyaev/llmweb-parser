"""Сериализация ``DomainMarkup`` в формат TOON.

TOON — самописный YAML-подобный формат из ТЗ Parsmart. Отличия от YAML:

* Массивы декларируются с размером: ``key[N]:``.
* Элементы массива нумеруются: ``1:``, ``2:``.
* Строковые значения всегда в двойных кавычках.

Пример (из исходного ``domains.toon``):

    domains[1]:
        1:
            mainPageUrl: "http://3bee-studio.com"
            device: "MOBILE"
            productPage:
                name: ".product-info h1"
                price: ".product-info .price"

Парсер TOON для курсового не нужен (мы только записываем) — он
предусмотрен заглушкой ``loads_value`` для будущих доработок.
"""

from typing import List, Sequence

from app.models.schemas import (
    CartPageSelectors,
    DomainMarkup,
    OrderPageSelectors,
    ProductPageSelectors,
)

INDENT = "    "


def dump_domains(domains: Sequence[DomainMarkup]) -> str:
    """Сериализует список разметок в TOON-текст."""
    lines: List[str] = [f"domains[{len(domains)}]:"]
    for idx, domain in enumerate(domains, start=1):
        lines.append(f"{INDENT}{idx}:")
        _write_domain(domain, depth=2, out=lines)
    lines.append("")
    return "\n".join(lines)


def _write_domain(domain: DomainMarkup, depth: int, out: List[str]) -> None:
    pad = INDENT * depth
    out.append(f'{pad}mainPageUrl: "{_escape(domain.mainPageUrl)}"')
    out.append(f'{pad}device: "{_escape(domain.device)}"')

    if domain.markdownComments:
        out.append(f"{pad}markdownComments[{len(domain.markdownComments)}]:")
        for i, c in enumerate(domain.markdownComments, start=1):
            out.append(f'{pad}{INDENT}{i}: "{_escape(c)}"')

    out.append(f"{pad}productPage:")
    _write_product_page(domain.productPage, depth + 1, out)

    if domain.cartPage:
        out.append(f"{pad}cartPage:")
        _write_cart_page(domain.cartPage, depth + 1, out)
    if domain.orderPage:
        out.append(f"{pad}orderPage:")
        _write_order_page(domain.orderPage, depth + 1, out)


def _write_product_page(pp: ProductPageSelectors, depth: int, out: List[str]) -> None:
    pad = INDENT * depth
    # Порядок полей выбран таким же, как в исходном domains.toon из ТЗ.
    for key in ("category", "currency", "name", "pictures", "price", "article"):
        value = getattr(pp, key)
        if value is None:
            continue
        out.append(f'{pad}{key}: "{_escape(value)}"')
    if pp.regEx:
        out.append(f"{pad}regEx[{len(pp.regEx)}]:")
        for i, r in enumerate(pp.regEx, start=1):
            out.append(f'{pad}{INDENT}{i}: "{_escape(r)}"')


def _write_cart_page(cp: CartPageSelectors, depth: int, out: List[str]) -> None:
    pad = INDENT * depth
    if cp.currency is not None:
        out.append(f'{pad}currency: "{_escape(cp.currency)}"')
    if cp.multiplyItemsPrice is not None:
        out.append(f"{pad}multiplyItemsPrice: {'true' if cp.multiplyItemsPrice else 'false'}")
    for key in ("prices", "quantities", "titles", "totalPrices", "urlTemplate"):
        v = getattr(cp, key)
        if v is not None:
            out.append(f'{pad}{key}: "{_escape(v)}"')


def _write_order_page(op: OrderPageSelectors, depth: int, out: List[str]) -> None:
    pad = INDENT * depth
    if op.confirmationElement is not None:
        out.append(f'{pad}confirmationElement: "{_escape(op.confirmationElement)}"')
    if op.regEx is not None:
        out.append(f'{pad}regEx: "{_escape(op.regEx)}"')


def _escape(value: str) -> str:
    """Экранируем двойные кавычки внутри строк."""
    return str(value).replace('"', '\\"')


def loads_value(line: str) -> str:
    """Минимальный парсер: удаляет внешние кавычки у строкового значения."""
    line = line.strip()
    if line.startswith('"') and line.endswith('"'):
        return line[1:-1].replace('\\"', '"')
    return line
