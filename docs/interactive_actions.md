# Интерактивные действия (модернизация май 2026)

Документ описывает модуль `interact`, расширяющий парсер до уровня
полноценного browser-automation: клики по кнопкам, добавление в корзину,
переходы между страницами. Реализовано в ветке
`feature/interactive-actions`.

## Цель доработки

По ТЗ преподавателя — расширить парсер так, чтобы он мог не только
читать страницу, но и **взаимодействовать с ней**: нажимать кнопки,
добавлять товар в корзину. При этом архитектура должна оставаться
универсальной — работать с разными интернет-магазинами **без
переписывания кода** под каждый сайт.

## Поддерживаемые действия

| `action` | Что делает |
|---|---|
| `add_to_cart` | Найти и нажать кнопку «В корзину» / «Add to cart» / «Купить» |
| `buy_now` | Кнопка «Купить сейчас» / «Checkout» / «Proceed to pay» |
| `open_product` | Перейти в карточку товара («Подробнее» / «View details») |
| `click_text` | Универсальный клик по тексту (передаётся в `text_hint`) |
| `custom_selector` | Кликнуть по явному CSS-селектору (для программных сценариев) |

## Стратегия поиска элементов

Главная фишка системы — она **сама** подбирает CSS-селектор кнопки, без
заранее заданных правил под конкретный магазин. Стратегия трёхэтапная:

```
1. Эвристический scoring (быстро, бесплатно)
   ↓ если score < 30
2. LLM-fallback (GPT-4o-mini или Llama-3.1-8B)
   ↓ если confidence < 50
3. status=not_found  (честно говорим что элемент не нашли)
```

### Эвристика (`app/scraper/element_finder.py`)

Каждый потенциально-кликабельный элемент (`button`, `a`,
`input[type=submit]`, `div[role=button]`, `div[onclick]`) получает баллы
по нескольким признакам:

| Признак | Баллы |
|---|---|
| **Текст** — точное совпадение с одной из фраз намерения | +20 |
| **Текст** — содержит фразу | +15 |
| **Текст** — все слова фразы встречаются | +8 |
| `aria-label` совпадает с фразой | ×0.9 от текста |
| `title` атрибут совпадает | ×0.7 |
| **Класс/id** соответствует regex-паттерну (`add-to-cart`, `buy-now`, ...) | +25 |
| `role="button"` | +5 |
| Тег `<button>` | +10 |
| `<input type="submit">` | +8 |
| `<a>` | +4 |
| **Anti-pattern**: текст содержит «избранное» / «wishlist» / «compare» | −50 |
| `disabled` атрибут | −30 |
| Класс содержит «disabled» | −10 |
| Глубокая вложенность (≥12 уровней) | −3 |

Минимальный confident score = **30**. Ниже — переключаемся на LLM.

### Многоязычные фразы

Из коробки система знает фразы на трёх языках:

```python
ADD_TO_CART_TEXTS = {
    # Русский
    "в корзину", "добавить в корзину", "купить", "заказать", ...
    # English
    "add to cart", "add to bag", "buy now", "purchase", ...
    # Прочие
    "ajouter au panier", "in den warenkorb", "agregar al carrito",
}
```

Добавить новый язык — буквально одна строка в `INTENTS`.

### LLM-fallback (`LLMParser.find_interactive_element`)

Если эвристика не уверена, спрашиваем LLM:

> На этой странице есть кнопка «{intent}»? Если да, верни CSS-селектор
> и confidence от 0 до 100. Избегай auto-generated классов вида
> `.css-1a2b3c`.

LLM возвращает JSON `{selector, confidence, reasoning}`. Если confidence
< 50 — считаем что элемент не найден.

### Сборка надёжного CSS-селектора

`build_selector(tag)` строит уникальный селектор для элемента в порядке
приоритета:

1. `#elementId` — если есть осмысленный id (не auto-generated хэш).
2. `tag[data-testid="..."]` — стабильный test-id.
3. `tag[data-action="..."]` — частое явное обозначение action'а.
4. `input[name="..."]` — для input полей.
5. `tag.class1.class2` — комбинация осмысленных классов.
6. `#anchorAncestor tag.cls` — путь от ближайшего предка с id.

**Игнорируются** хэш-классы:
- `css-1a2b3c` (CSS-in-JS / emotion)
- `sc-aBcD1` (styled-components)
- `jsx-1234567890` (Next.js JSX)
- `MuiButton-root-12345` (Material-UI)

## Browser-automation (`app/scraper/interactor.py`)

```
1. async_playwright.chromium.launch(headless=True, stealth-args)
2. context = new_context(viewport, locale='ru-RU', стелс-init-script)
3. page.goto(url, wait_until='networkidle')
4. page.wait_for_timeout(1500)   # для отложенных скриптов
5. html = await page.content()
6. cleaned = clean_html(html, keep_interactive=True)
7. selector = element_finder.find_best(cleaned, intent)
   ↳ если score < 30:
       selector = await llm.find_interactive_element(cleaned, intent)
8. page.wait_for_selector(selector)
9. page.click(selector)
10. page.wait_for_load_state('networkidle')   # дать корзине обновиться
11. result.page_title_after = await page.title()
```

После каждого шага вызывается `log_step(step, detail)` — это и есть
подробный технический лог, который сохраняется в БД и отдаётся клиенту.

## Универсальная очистка HTML

В обычном режиме `clean_html` агрессивно вырезает `<button>`, `<input>`,
`<form>` — для извлечения цены они шум. Но для interactor — это
**основной материал**. Поэтому добавлен флаг:

```python
clean_html(html, max_length=20_000, keep_interactive=True)
```

В этом режиме сохраняются: `button`, `input`, `form`, `a`, плюс атрибуты
`aria-label`, `role`, `data-action`, `data-testid`, `onclick`,
`disabled`, `name`, `type` — всё что нужно эвристике.

## REST API

### `POST /api/interact`

Универсальная ручка для любого действия.

```json
{
  "url": "https://shop.example.com/product/123",
  "action": "add_to_cart",
  "selector": null,
  "text_hint": null,
  "intent": null,
  "use_llm_fallback": true
}
```

Ответ:

```json
{
  "id": 42,
  "status": "success",
  "url": "...",
  "action": "add_to_cart",
  "selector_used": "#addToCartBtn",
  "selector_source": "heuristic",
  "selector_confidence": 85.0,
  "element_text": "В корзину",
  "page_title_before": "Кофемашина Bosch TKA8633",
  "page_title_after": "Кофемашина Bosch TKA8633 — добавлено в корзину",
  "duration_ms": 4687,
  "log": [
    {"timestamp_ms": 825, "step": "goto", "detail": "..."},
    {"timestamp_ms": 3568, "step": "page_loaded", "detail": "..."},
    {"timestamp_ms": 3578, "step": "heuristic", "detail": "..."},
    {"timestamp_ms": 3580, "step": "candidate", "detail": "score=55.0 ..."},
    {"timestamp_ms": 3599, "step": "click", "detail": "..."},
    {"timestamp_ms": 4687, "step": "done", "detail": "..."}
  ],
  "timestamp": "2026-05-21T16:00:00Z"
}
```

`status` ∈ `{success, not_found, error}`. **HTTP 200 даже при error /
not_found** — это сознательное решение по ТЗ («корректный JSON-ответ
даже при неудаче»).

### `POST /api/cart/add` (шорткат)

Эквивалентно `/api/interact` с `action=add_to_cart`. Просто меньше
печатать.

```json
{ "url": "https://shop.example.com/product/123" }
```

### `GET /api/interactions/runs`

История всех интерактивных действий с фильтрацией.

```
GET /api/interactions/runs?limit=50&offset=0
```

### `GET /api/interactions/runs/{id}`

Детали конкретного действия (включая полный технический лог).

## Хранение в БД

Таблица `interaction_runs`:

```sql
CREATE TABLE interaction_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    url                  TEXT NOT NULL,
    action               TEXT NOT NULL,
    status               TEXT NOT NULL,
    selector_used        TEXT,
    selector_source      TEXT,   -- heuristic | llm | text-match | user
    selector_confidence  REAL,
    element_text         TEXT,
    page_title_before    TEXT,
    page_title_after     TEXT,
    error                TEXT,
    duration_ms          INTEGER,
    log_json             TEXT,   -- полный лог как JSON
    created_at           TEXT NOT NULL
);
```

Индексы: `url`, `created_at`.

## Тестирование

Добавлено **29 новых тестов**, всего в проекте теперь **139 автотестов**:

| Файл | Тестов | Описание |
|---|---:|---|
| `tests/test_element_finder.py` | 20 | Scoring, многоязычность, anti-patterns, build_selector, autogen-классы |
| `tests/test_interact_api.py` | 9 | Endpoints с моком Playwright (success / not_found / error / шорткат / история) |

E2E проверка на локальной фикстуре с реальной кнопкой:

```
status              = success
selector_used       = #addToCartBtn
selector_source     = heuristic
selector_confidence = 55.0
element_text        = В корзину
page_title_before   = Кофемашина Bosch TKA8633 — Sample Shop
page_title_after    = Кофемашина Bosch TKA8633 — добавлено в корзину
duration_ms         = 4687
```

E2E проверка на toscrape.com (страница книги, где **нет** кнопки
add-to-cart):

```
status        = not_found
selector_source     = None
log           = [..., 'llm: selector=None confidence=0.0 reasoning=not found']
```

Система **корректно не нашла кнопку и не наврала** — это важный показатель
качества: LLM согласилась с эвристикой, что элемента нет.

## Универсальность

Чтобы добавить поддержку нового магазина, **ничего менять не надо**.
Система сама подберёт селектор по тексту и атрибутам. Покрытие из коробки:

* Любой магазин с английскими/русскими кнопками.
* Любой framework на фронте: React / Vue / Angular / Next.js (хэш-классы
  игнорируются автоматически).
* Любая разметка: семантические `<button>`, `<a>`, `<input type=submit>`
  или div-ы с `role="button"`.

Когда добавить НУЖНО:
* **Anti-pattern** мешает — добавить слово в `NOT_CART_TEXTS`.
* **Новое намерение** (например, «оставить отзыв») — добавить запись в
  `INTENTS` (5 строк).
* **Совсем нестандартный селектор** — пользователь может передать его
  через `action=custom_selector`.

## Расширение

```python
# Добавить новый язык
ADD_TO_CART_TEXTS.add("dodaj do koszyka")  # польский

# Добавить новое намерение
INTENTS["leave_review"] = Intent(
    name="leave_review",
    texts={"оставить отзыв", "leave a review", "написать отзыв"},
    class_patterns=[r"review[-_]?(add|btn)?", r"leave[-_]?feedback"],
)
```

Никаких изменений в `interactor.py`, `routes.py` или БД — всё работает
автоматически.

## Что НЕ покрыто (намеренно)

* **Multistep flows** (открыть → выбрать размер → добавить в корзину) —
  для курсового достаточно одного клика. Архитектура легко расширяется:
  `InteractRequest.actions: List[ActionStep]`.
* **Авторизация** перед действием (login + cookies) — отдельная задача,
  выходит за рамки ТЗ.
* **Скриншоты до/после** — Playwright это умеет (`page.screenshot()`),
  легко добавить, но раздувает размер БД.
* **Прокси/антикапча** — для крупных маркетплейсов отдельная задача
  (см. `docs/testing_results.md` про Wildberries).
