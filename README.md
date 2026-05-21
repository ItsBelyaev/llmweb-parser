# LLM Web Parser — сервис автоматизированной разметки веб-страниц

Курсовой проект студентов НИУ ВШЭ, 2026.
Реализация задания **Parsmart**: сервис, который по образцу страницы
товара автоматически генерирует CSS-селекторы для всех его полей и
позволяет переиспользовать их на любых страницах того же домена.

---

## 🚀 Что умеет

Сервис поддерживает **два режима** работы:

### ⚡ Режим 1: прямое извлечение

Загружаем страницу через Playwright, очищаем HTML, передаём в LLM (Llama
3.1 8B Instruct) и получаем структурированные данные: название, цена,
артикул, URL изображения.

- ✅ Работает на **любом** e-commerce сайте без предварительной настройки.
- ❌ Каждый запрос требует обращения к LLM (медленно, дорого).

### 🎯 Режим 2: CSS-селекторы (основной по ТЗ Parsmart)

Один раз спрашиваем у LLM CSS-селекторы для опорной страницы товара.
Сохраняем их в БД и формируем `domains.toon`-совместимый конфиг.
Дальнейшие запросы используют сохранённые селекторы и **не вызывают LLM**.

- ✅ В десятки раз быстрее: чистый BeautifulSoup + SQLite.
- ✅ Дёшево: один вызов LLM покрывает сотни страниц одного домена.
- ✅ Прозрачно: можно посмотреть/отредактировать селекторы вручную.
- ✅ Устойчиво: автоматическая валидация селекторов (если LLM «галлюцинировал»
  несуществующий класс, поле сбрасывается в `null` и фиксируется комментарий).

---

## 🛠 Стек технологий

| Компонент | Технология |
|-----------|-----------|
| Backend | **FastAPI** + Uvicorn (async ASGI) |
| Валидация | **Pydantic v2** |
| База данных | **SQLite** через aiosqlite (async) |
| LLM-интеграция | **LangChain** + **HuggingFace Hub** (с fallback) |
| Парсинг | **Playwright** (headless Chromium) |
| Предобработка HTML | **BeautifulSoup4** + lxml |
| Шаблоны | `templates/dashboard.html` (vanilla JS, без сборки) |
| Тестирование | **pytest** + pytest-asyncio + httpx (110 тестов) |
| Деплой | **Docker** + docker-compose |
| Конфигурация | python-dotenv (`.env`) |

LLM-провайдеры:
- **HuggingFace Inference**: Llama 3.1 8B Instruct, Llama 3.2 3B / 1B, Qwen 2.5 7B.
- **OpenAI** (опционально): GPT-4o-mini — добавьте `OPENAI_API_KEY`, провайдер
  выбирается автоматически.

---

## 📂 Структура проекта

```
llmweb_parser/
├── app/                          # основной пакет
│   ├── main.py                   # FastAPI приложение + lifespan
│   ├── api/routes.py             # REST endpoints (/api/*)
│   ├── llm/parser.py             # LLMParser, fallback, _extract_json
│   ├── models/schemas.py         # Pydantic-модели (ProductData, DomainMarkup, ...)
│   ├── prompts/templates.py      # LangChain PromptTemplate'ы
│   ├── scraper/
│   │   ├── fetcher.py            # Playwright + stealth + retry
│   │   ├── cleaner.py            # очистка HTML, smart_truncate
│   │   └── applier.py            # применение и валидация CSS-селекторов
│   ├── toon/serializer.py        # сериализатор формата TOON (ТЗ Parsmart)
│   ├── database/db.py            # aiosqlite + CRUD таблиц
│   └── utils/logger.py           # настройка логирования
├── templates/dashboard.html      # UI с тремя вкладками
├── tests/                        # 110 тестов pytest
│   ├── conftest.py               # фикстуры httpx + временная БД
│   ├── test_schemas.py           # валидация Pydantic-моделей
│   ├── test_cleaner.py           # очистка HTML
│   ├── test_llm_parser.py        # JSON-парсер + нормализация
│   ├── test_database.py          # SQLite CRUD
│   ├── test_api.py               # /api/parse, /api/results, ...
│   ├── test_applier.py           # CSS-селекторы (apply/validate)
│   ├── test_toon.py              # сериализация TOON
│   ├── test_selectors_api.py     # /api/selectors/*
│   └── fixtures/                 # HTML-фикстуры для интеграционных тестов
├── docs/                         # отчёты и материалы
│   ├── analogs.md                # обзор аналогов
│   ├── architecture.md           # архитектура и решения
│   └── testing.md                # методология тестирования
├── run.py                        # uvicorn точка входа
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pytest.ini
├── .env.example
└── README.md
```

---

## ⚡ Быстрый старт

### Локальный запуск

```bash
# 1. Клонировать репозиторий
git clone https://github.com/<user>/llmweb_parser.git
cd llmweb_parser

# 2. Создать виртуальное окружение (Python 3.11 или 3.12)
python3.12 -m venv venv
source venv/bin/activate      # macOS/Linux
# venv\Scripts\activate       # Windows

# 3. Установить зависимости
pip install -r requirements.txt
playwright install chromium

# 4. Настроить ключи
cp .env.example .env
# Вписать HUGGINGFACE_API_KEY (получить: https://huggingface.co/settings/tokens)

# 5. Запустить
uvicorn app.main:app --reload
# или
python run.py
```

Открыть в браузере: **http://localhost:8000**
Swagger UI: **http://localhost:8000/docs**

### Docker

```bash
docker-compose up --build
```

---

## 🔌 REST API

Полная документация генерируется автоматически: `GET /docs` (Swagger UI).

### Прямое извлечение

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/api/parse` | Запустить извлечение через LLM |
| `GET` | `/api/results` | История результатов (с пагинацией) |
| `GET` | `/api/results/{id}` | Конкретный результат |
| `DELETE` | `/api/results` | Очистить историю |

### CSS-селекторы (Parsmart)

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/api/selectors/generate` | Сгенерировать селекторы для домена по опорной странице |
| `POST` | `/api/selectors/apply` | Применить сохранённые селекторы к новому URL (без LLM) |
| `GET` | `/api/selectors` | Список всех сохранённых разметок |
| `GET` | `/api/selectors/{domain}` | Получить разметку конкретного домена |
| `DELETE` | `/api/selectors/{domain}` | Удалить разметку |
| `GET` | `/api/selectors/export.toon` | Скачать все разметки в формате TOON |

### Журнал действий

| Метод | URL | Описание |
|-------|-----|----------|
| `POST` | `/api/interactions` | Записать действие пользователя |
| `GET` | `/api/interactions` | История действий |
| `GET` | `/health` | Health-check |

---

## 🧪 Тестирование

```bash
pytest tests/ -v
```

**Покрытие:** 110 тестов, 8 модулей (см. `docs/testing.md`).

Категории:
- **Unit**: схемы Pydantic, очистка HTML, JSON-парсер, нормализация цены/артикула, TOON-сериализатор.
- **Integration**: FastAPI endpoints через httpx, БД через aiosqlite, mock LLM и Playwright.
- **End-to-end**: ручной прогон по локальной HTML-фикстуре (`tests/fixtures/sample_product.html`).

---

## 📖 Документация

- [docs/analogs.md](docs/analogs.md) — обзор существующих аналогов (Scrapy, Octoparse, ScrapingBee, Diffbot, …) и сравнение
- [docs/architecture.md](docs/architecture.md) — подробное описание архитектуры и проектных решений
- [docs/testing.md](docs/testing.md) — методология тестирования, фикстуры, результаты

---

## ⚖️ Лицензия

MIT. Используйте ответственно: не нарушайте `robots.txt` и условия использования
сайтов, не злоупотребляйте при коммерческом парсинге.
