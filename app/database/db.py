"""
Работа с базой данных SQLite через aiosqlite
"""

import aiosqlite
import logging
import os
from datetime import datetime, timezone
from typing import Optional, List

logger = logging.getLogger(__name__)


def _db_path() -> str:
    """Читаем путь к БД динамически, чтобы тесты могли переопределить DB_PATH"""
    return os.getenv("DB_PATH", "llmweb.db")


async def get_db() -> aiosqlite.Connection:
    """Получение соединения с базой данных"""
    conn = await aiosqlite.connect(_db_path())
    conn.row_factory = aiosqlite.Row
    return conn


async def init_db() -> None:
    """Инициализация схемы базы данных"""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS parsed_products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'other',
                status      TEXT NOT NULL DEFAULT 'success',
                error       TEXT,
                title       TEXT,
                price       REAL,
                article     TEXT,
                image_url   TEXT,
                raw_response TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                action     TEXT NOT NULL,
                payload    TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # Разметки доменов в формате Parsmart (CSS-селекторы)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS domain_markups (
                domain      TEXT PRIMARY KEY,
                json        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        # Журнал интерактивных действий (ТЗ май 2026)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS interaction_runs (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                url                  TEXT NOT NULL,
                action               TEXT NOT NULL,
                status               TEXT NOT NULL,
                selector_used        TEXT,
                selector_source      TEXT,
                selector_confidence  REAL,
                element_text         TEXT,
                page_title_before    TEXT,
                page_title_after     TEXT,
                error                TEXT,
                duration_ms          INTEGER,
                log_json             TEXT,
                created_at           TEXT NOT NULL
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_interaction_url ON interaction_runs(url)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_interaction_created ON interaction_runs(created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_url ON parsed_products(url)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_created ON parsed_products(created_at)"
        )
        await db.commit()
    logger.info("✅ Таблицы БД готовы (SQLite: %s)", _db_path())


# ─── CRUD для parsed_products ────────────────────────────────────────────────


async def save_parse_result(
    url: str,
    source: str,
    status: str,
    error: Optional[str] = None,
    title: Optional[str] = None,
    price: Optional[float] = None,
    article: Optional[str] = None,
    image_url: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> int:
    """Сохраняет результат парсинга, возвращает id записи"""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            """INSERT INTO parsed_products
               (url, source, status, error, title, price, article, image_url, raw_response, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (url, source, status, error, title, price, article, image_url, raw_response, now),
        )
        await db.commit()
        row_id = cursor.lastrowid
    logger.info("💾 Сохранён результат парсинга id=%s url=%s", row_id, url)
    return row_id


async def get_parse_results(limit: int = 50, offset: int = 0) -> List[dict]:
    """Получает историю результатов парсинга"""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM parsed_products ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_parse_result_by_id(record_id: int) -> Optional[dict]:
    """Получает запись по id"""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM parsed_products WHERE id = ?", (record_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def count_parse_results() -> int:
    """Общее количество записей парсинга"""
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM parsed_products")
        row = await cursor.fetchone()
    return row[0] if row else 0


# ─── CRUD для interactions ───────────────────────────────────────────────────


async def save_interaction(action: str, payload: Optional[str] = None) -> int:
    """Сохраняет пользовательское действие"""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "INSERT INTO interactions (action, payload, created_at) VALUES (?, ?, ?)",
            (action, payload, now),
        )
        await db.commit()
        row_id = cursor.lastrowid
    logger.info("💾 Действие сохранено id=%s action=%s", row_id, action)
    return row_id


async def get_interactions(limit: int = 50, offset: int = 0) -> List[dict]:
    """Получает историю действий"""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM interactions ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def count_interactions() -> int:
    """Общее количество записей действий"""
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM interactions")
        row = await cursor.fetchone()
    return row[0] if row else 0


# ─── CRUD для domain_markups (CSS-селекторы) ─────────────────────────────────


import json as _json  # noqa: E402
from urllib.parse import urlparse  # noqa: E402


def _domain_key(url_or_domain: str) -> str:
    """Нормализованный ключ домена (без схемы и www)."""
    s = url_or_domain.strip()
    if "://" in s:
        s = urlparse(s).netloc
    s = s.lower()
    if s.startswith("www."):
        s = s[4:]
    return s


async def save_domain_markup(domain: str, markup_dict: dict) -> None:
    """Сохраняет разметку домена (UPSERT по ключу).

    ``markup_dict`` — это сериализованный ``DomainMarkup.model_dump()``.
    """
    key = _domain_key(domain)
    now = datetime.now(timezone.utc).isoformat()
    blob = _json.dumps(markup_dict, ensure_ascii=False)
    async with aiosqlite.connect(_db_path()) as db:
        # SQLite поддерживает UPSERT через ON CONFLICT с 3.24+
        await db.execute(
            """INSERT INTO domain_markups (domain, json, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                   json = excluded.json,
                   updated_at = excluded.updated_at""",
            (key, blob, now, now),
        )
        await db.commit()
    logger.info("💾 Разметка сохранена для домена: %s", key)


async def get_domain_markup(domain_or_url: str) -> Optional[dict]:
    """Возвращает разметку (словарь) для указанного домена или None."""
    key = _domain_key(domain_or_url)
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "SELECT json FROM domain_markups WHERE domain = ?", (key,)
        )
        row = await cursor.fetchone()
    if not row:
        return None
    try:
        return _json.loads(row[0])
    except Exception as exc:  # noqa: BLE001
        logger.error("Не удалось распарсить разметку %s: %s", key, exc)
        return None


async def list_domain_markups() -> List[dict]:
    """Возвращает все разметки в виде списка словарей."""
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "SELECT json FROM domain_markups ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
    result: List[dict] = []
    for r in rows:
        try:
            result.append(_json.loads(r[0]))
        except Exception:  # noqa: BLE001
            continue
    return result


async def delete_domain_markup(domain_or_url: str) -> bool:
    """Удаляет разметку, возвращает True если что-то было удалено."""
    key = _domain_key(domain_or_url)
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "DELETE FROM domain_markups WHERE domain = ?", (key,)
        )
        await db.commit()
        return cursor.rowcount > 0


# ─── CRUD для interaction_runs (интерактивные действия) ──────────────────────


async def save_interaction_run(
    *,
    url: str,
    action: str,
    status: str,
    selector_used: Optional[str] = None,
    selector_source: Optional[str] = None,
    selector_confidence: Optional[float] = None,
    element_text: Optional[str] = None,
    page_title_before: Optional[str] = None,
    page_title_after: Optional[str] = None,
    error: Optional[str] = None,
    duration_ms: int = 0,
    log: Optional[list] = None,
) -> int:
    """Сохраняет запись о выполненном интерактивном действии.

    Возвращает id новой записи. Никогда не падает (ошибки логирует).
    """
    now = datetime.now(timezone.utc).isoformat()
    log_json = _json.dumps(log or [], ensure_ascii=False)
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            """INSERT INTO interaction_runs
               (url, action, status, selector_used, selector_source,
                selector_confidence, element_text, page_title_before,
                page_title_after, error, duration_ms, log_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                url, action, status, selector_used, selector_source,
                selector_confidence, element_text, page_title_before,
                page_title_after, error, duration_ms, log_json, now,
            ),
        )
        await db.commit()
        row_id = cursor.lastrowid
    logger.info("💾 Interaction run id=%s action=%s status=%s", row_id, action, status)
    return row_id


async def get_interaction_runs(limit: int = 50, offset: int = 0) -> List[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM interaction_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
    out: List[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["log"] = _json.loads(d.pop("log_json") or "[]")
        except Exception:
            d["log"] = []
            d.pop("log_json", None)
        out.append(d)
    return out


async def get_interaction_run_by_id(record_id: int) -> Optional[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM interaction_runs WHERE id = ?", (record_id,)
        )
        row = await cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["log"] = _json.loads(d.pop("log_json") or "[]")
    except Exception:
        d["log"] = []
        d.pop("log_json", None)
    return d


async def count_interaction_runs() -> int:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM interaction_runs")
        row = await cursor.fetchone()
    return row[0] if row else 0
