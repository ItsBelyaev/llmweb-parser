"""Одноразовая миграция значений source в parsed_products.

Сценарий: раньше у нас в поле `source` лежали имена конкретных
маркетплейсов (`ozon`, `wildberries`, `yandex_market`, ...). Сейчас
используются языковые категории (`russian`, `foreign`, `other`).

Эта миграция приводит существующие записи в БД к новому формату:

    ozon, wildberries, yandex_market, mvideo, dns        → russian
    international                                        → foreign
    auto                                                 → other
    остальные (включая уже корректные russian/foreign/other) — не трогаем

Запуск:

    cd llmweb_parser
    source venv/bin/activate
    python scripts/migrate_source.py            # на боевой БД (llmweb.db)
    python scripts/migrate_source.py --db x.db  # на произвольном файле
    python scripts/migrate_source.py --dry-run  # без записи

Скрипт безопасен для повторного запуска — выполняет идемпотентный
UPDATE по списку устаревших значений.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys


# Маппинг старых значений на новые.
LEGACY_MAP = {
    "ozon": "russian",
    "wildberries": "russian",
    "yandex_market": "russian",
    "mvideo": "russian",
    "dns": "russian",
    "international": "foreign",
    "auto": "other",
}


def migrate(db_path: str, dry_run: bool = False) -> int:
    """Выполняет миграцию. Возвращает суммарное число изменённых строк."""
    if not os.path.exists(db_path):
        print(f"[!] БД не найдена: {db_path} — пропускаем (это нормально для новой установки).")
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Проверяем, что таблица существует. На совсем чистой БД (где init_db
    # ещё не вызывался) — выходим.
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='parsed_products'"
    )
    if cur.fetchone() is None:
        print("[i] Таблица parsed_products отсутствует — нечего мигрировать.")
        conn.close()
        return 0

    total_changed = 0
    for old, new in LEGACY_MAP.items():
        cur.execute(
            "SELECT COUNT(*) FROM parsed_products WHERE source = ?", (old,)
        )
        n = cur.fetchone()[0]
        if n == 0:
            continue
        print(f"  {old:18s} → {new:8s}  ({n} строк)")
        if not dry_run:
            cur.execute(
                "UPDATE parsed_products SET source = ? WHERE source = ?",
                (new, old),
            )
        total_changed += n

    if dry_run:
        conn.rollback()
        print(f"[dry-run] Изменений не сохранено. Всего бы тронули: {total_changed}")
    else:
        conn.commit()
        print(f"[ok] Готово. Обновлено строк: {total_changed}")

    # Итоговая раскладка после миграции
    cur.execute(
        "SELECT source, COUNT(*) FROM parsed_products GROUP BY source ORDER BY 2 DESC"
    )
    rows = cur.fetchall()
    if rows:
        print("\nТекущее распределение по source:")
        for src, cnt in rows:
            print(f"  {src or '(NULL)':20s} {cnt}")

    conn.close()
    return total_changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "llmweb.db"),
        help="Путь к SQLite-файлу (по умолчанию: DB_PATH из env или llmweb.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что было бы изменено; ничего не пишем.",
    )
    args = parser.parse_args(argv)

    print(f"БД: {args.db}{' (dry-run)' if args.dry_run else ''}\n")
    migrate(args.db, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
