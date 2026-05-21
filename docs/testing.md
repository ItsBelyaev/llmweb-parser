# Методология тестирования

Тестирование разделено на три уровня: **unit**, **integration** и
**end-to-end**. Все тесты автоматизированы через pytest и запускаются
одной командой:

```bash
pytest tests/ -v
```

**Итог: 110 тестов, все проходят за ~0.7 секунды.**

---

## 1. Unit-тесты

Покрывают отдельные функции и классы, без сети и без БД.

### tests/test_schemas.py — Pydantic-модели (16 тестов)

Проверяет валидацию входящих и исходящих данных:

* `ParseRequest`: URL должен начинаться с http(s)://, не быть пустым,
  не превышать 2048 символов; `source` нормализуется к одному из
  допустимых значений или сбрасывается в `other`.
* `ProductData`: цена должна быть положительной и реалистичной
  (`< 10 млн`), округляется до 2 знаков; некорректный URL изображения
  становится `None`; пустое название становится `None`.
* `InteractionCreate`: пустое действие — ошибка, длинное обрезается до
  100 символов.

### tests/test_cleaner.py — очистка HTML (11 тестов)

* Тег `<script>` удаляется, но текст остаётся.
* `<style>` удаляется.
* Атрибут `src` у `<img>` сохраняется.
* Пустые `<div>` удаляются.
* HTML длиннее лимита усекается с маркером `truncated`.
* Тег `<nav>` удаляется.
* Эвристики `extract_product_links` и `is_product_page` работают
  корректно на типичных страницах.

### tests/test_llm_parser.py — LLM-обёртка (12 тестов)

* `_extract_json` парсит чистый JSON, JSON в markdown-блоках, JSON с
  trailing comma, JSON с пояснительным текстом, JSON с null-полями.
* `_normalize`: «1 990₽» → 1990.0, альтернативные имена полей (`name`
  вместо `title`, `sku` вместо `article`) подхватываются.
* `extract_product_data` использует LangChain-цепочку, а при её ошибке
  переключается на прямой fallback. Если оба способа недоступны —
  возвращается пустой результат, а не исключение.

### tests/test_applier.py — CSS-селекторы (19 тестов)

* `validate_selectors` оставляет рабочие селекторы, отбрасывает
  ничего-не-нашедшие и невалидные с записью в `markdownComments`.
* `apply_selectors` извлекает title/price/article/image_url и
  абсолютизирует относительные URL изображений.
* `_normalize_price` корректно парсит «1 999 ₽», «1 999,99 ₽», «1.999,99 ₽»,
  чистые числа.
* `_normalize_article` сохраняет alphanumeric SKU («SM-A55»),
  склеивает «2006 4875» → «20064875».
* `_absolutize` обрабатывает относительные, protocol-relative и
  абсолютные URL.

### tests/test_toon.py — TOON-сериализатор (12 тестов)

* Пустой список → `domains[0]:`.
* Один домен → `domains[1]:` и блок `1:` внутри.
* `mainPageUrl`, `device`, `markdownComments` корректно сериализуются.
* Null-селекторы НЕ попадают в выход.
* Комментарии нумеруются с 1.
* Кавычки внутри значений экранируются.
* `cartPage` и `orderPage` сериализуются если заданы.
* Порядок полей `productPage` стабилен: `category, currency, name,
  pictures, price, article` (как в исходном `domains.toon` из ТЗ).

---

## 2. Integration-тесты

Используют реальный экземпляр FastAPI приложения, реальную SQLite во
временном файле, но мокают внешние вызовы (Playwright, LLM API).

### tests/test_database.py — SQLite CRUD (8 тестов)

* Сохранение и чтение `parsed_products` по ID.
* Сохранение результата с ошибкой (без полей товара).
* Список результатов — отсортирован по убыванию даты.
* Счётчик корректно растёт при добавлении.
* Несуществующий ID → `None`.
* Сохранение и список `interactions`.
* Сохранение `raw_response` от LLM.

### tests/test_api.py — основные endpoints (12 тестов)

* `GET /health` → 200 OK + версия.
* `GET /api/results` → пустой список изначально.
* `POST /api/parse` с невалидным URL → 422.
* `POST /api/parse` с пустым URL → 422.
* `POST /api/parse` с моками Playwright и LLM → 200 + record в БД.
* `POST /api/parse` при ошибке Playwright → 200 со `status: error`.
* `GET /api/results/{id}` → 200 после успешного парсинга.
* `GET /api/results/999999` → 404.
* `POST /api/interactions` → 200.
* `POST /api/interactions` с пустым action → 422.
* `GET /api/interactions` → 200.
* `DELETE /api/results` → 200, потом список пустой.

### tests/test_selectors_api.py — CSS-селекторы API (9 тестов)

* `POST /api/selectors/generate` с невалидным URL → 422.
* `POST /api/selectors/generate` (mock LLM) → разметка сохраняется в БД.
* `POST /api/selectors/generate` с галлюцинированным селектором →
  селектор сбрасывается в `null`, добавляется коммент.
* `POST /api/selectors/generate` при сетевой ошибке → 200 со
  `status: error`.
* `POST /api/selectors/apply` использует сохранённую разметку и **не
  вызывает LLM** (проверяется через mock-спай).
* `POST /api/selectors/apply` без сохранённой разметки → 404.
* `GET /api/selectors` возвращает все сохранённые домены.
* `GET /api/selectors/{domain}` — конкретный домен.
* `DELETE /api/selectors/{domain}` — удаление + повторный 404.
* `GET /api/selectors/export.toon` → формат начинается с `domains[`.

---

## 3. End-to-end проверка (ручная)

В дополнение к автоматическим тестам выполнена ручная проверка всего
пайплайна на реальной HTML-фикстуре.

### Сценарий

1. Поднять Uvicorn:
   ```bash
   uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
2. Поднять статический HTTP-сервер с тестовой страницей:
   ```bash
   cd tests/fixtures && python3 -m http.server 8765
   ```
3. Вызвать `POST /api/parse`:
   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/parse \
        -H "Content-Type: application/json" \
        -d '{"url":"http://127.0.0.1:8765/sample_product.html","source":"other"}'
   ```

### Результат прямого извлечения

| Поле | Извлечено | Эталон |
|------|-----------|--------|
| title | «Электрогриль Tefal OptiGrill Elite GC750D30» | ✅ совпадает |
| price | null | ⚠️ модель не подобрала (две цены на странице) |
| article | «2006 4875» | ✅ найдено |
| image_url | `https://cdn.example.com/.../grill.jpg` | ✅ совпадает |

### Результат генерации селекторов

| Поле | Селектор | Валидация |
|------|----------|-----------|
| name | `.m-product .main .name` | ✅ совпадает с эталоном ТЗ |
| article | `.orig-info .orig-info-num` | ✅ работает |
| pictures | `#m-zoomable-image picture img` | ✅ совпадает с эталоном ТЗ |
| category | `.breadcrumbs a:nth-child(3)` | ✅ работает |
| price | ничего | ⚠️ селектор «галлюцинированный», отброшен, добавлен комментарий |

### Результат применения селекторов (без LLM)

```json
{
  "product": {
    "title": "Электрогриль Tefal OptiGrill Elite GC750D30",
    "article": "20064875",
    "image_url": "https://cdn.example.com/.../grill.jpg",
    "price": null
  }
}
```

Артикул автоматически нормализован: `«2006 4875» → «20064875»`.

### TOON-экспорт

```
domains[1]:
    1:
        mainPageUrl: "http://127.0.0.1:8765"
        device: "DESKTOP"
        markdownComments[1]:
            1: "price: селектор '...' ничего не нашёл"
        productPage:
            category: ".breadcrumbs a:nth-child(3)"
            name: ".m-product .main .name"
            pictures: "#m-zoomable-image picture img"
            article: ".orig-info .orig-info-num"
```

Структура **полностью совпадает** с эталоном `domains.toon` из ТЗ.

---

## Что НЕ покрыто тестами

| Не покрыто | Причина |
|------------|---------|
| Реальный вызов HuggingFace API | Зависит от внешнего сервиса, недетерминирован. Покрыт сценарием 3 (end-to-end вручную). |
| Реальные сайты (Wildberries, Ozon) | Зависит от антибот-защиты сайтов, которые блокируют тестовый трафик. |
| UI dashboard (Selenium) | Курсовой проект, UI — минимальный, протестирован руками. |
| Race conditions в SQLite | aiosqlite использует отдельное соединение на каждый запрос, race-conditions маловероятны для масштаба курсового. |

---

## Метрики

| Метрика | Значение |
|---------|----------|
| Всего тестов | **110** |
| Из них unit | 70 |
| Из них integration | 40 |
| Время прохождения | < 1 секунды |
| Тестируемых модулей | 8 (schemas, cleaner, llm parser, applier, toon, database, api, selectors api) |
| Замоканных внешних сервисов | 2 (Playwright fetch_page, HuggingFace LLM) |
