"""
Загрузка веб-страниц через Playwright (headless Chromium).

Используется обоими режимами парсинга:
* `direct` — POST /api/parse (прямое извлечение через LLM);
* `selectors` — POST /api/selectors/generate (поиск CSS-селекторов).

Для повышения шансов пройти антибот-защиты крупных маркетплейсов:

* `playwright-stealth` — патчит десятки JS-признаков автоматизации.
* Реалистичный набор HTTP-заголовков Chromium 121 на macOS.
* `timezone_id="Europe/Moscow"`, локаль `ru-RU`.
* Двухступенчатая стратегия ожидания: `domcontentloaded` →
  попытка `networkidle` с тайм-аутом.
"""

import logging
from typing import Optional

from playwright.async_api import (
    async_playwright,
    TimeoutError as PWTimeoutError,
)
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


# Реалистичный User-Agent (последний Chrome на macOS).
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def _realistic_headers() -> dict:
    return {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Not.A/Brand";v="99", "Chromium";v="121", "Google Chrome";v="121"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    reraise=True,
)
async def fetch_page(
    url: str,
    timeout: int = 60_000,
    wait_for_selector: Optional[str] = None,
    extra_wait_ms: int = 0,
) -> str:
    """Загружает страницу через Playwright + stealth.

    Args:
        url: целевой URL.
        timeout: общий тайм-аут на загрузку (мс).
        wait_for_selector: опциональный CSS-селектор, появления которого
            нужно дождаться. Полезно для медленных SPA вроде Wildberries.
        extra_wait_ms: дополнительная пауза после загрузки (мс).
            Рекомендуется 3000–8000 для крупных маркетплейсов.
    """
    logger.info("🌐 Загрузка страницы: %s", url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            user_agent=DEFAULT_UA,
            viewport={"width": 1366, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            extra_http_headers=_realistic_headers(),
        )

        # Применяем stealth-патчи (если установлен playwright-stealth).
        try:
            from playwright_stealth import Stealth

            await Stealth(
                navigator_user_agent_override=DEFAULT_UA,
            ).apply_stealth_async(context)
            logger.info("🥷 stealth применён")
        except ImportError:
            logger.info("playwright-stealth не установлен, пропускаем")
        except Exception as exc:  # noqa: BLE001
            logger.warning("stealth не удалось применить: %s", exc)

        try:
            page = await context.new_page()

            # Шаг 1: domcontentloaded — быстро и гарантированно.
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout,
                )
            except PWTimeoutError as exc:
                logger.warning("domcontentloaded timeout: %s — продолжаем", exc)

            # Шаг 2: пытаемся дойти до networkidle, но не блокируемся —
            # крупные SPA (Wildberries, Ozon) никогда не отдают
            # networkidle из-за бесконечного polling'а.
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeoutError:
                logger.info("networkidle не достигнут (SPA?) — продолжаем")

            # Шаг 3: опциональное ожидание конкретного селектора.
            if wait_for_selector:
                try:
                    await page.wait_for_selector(
                        wait_for_selector, timeout=15_000,
                    )
                except PWTimeoutError:
                    logger.warning(
                        "wait_for_selector '%s' не появился за 15с",
                        wait_for_selector,
                    )

            # Шаг 4: базовая пауза + дополнительная для упрямых сайтов.
            await page.wait_for_timeout(2000 + max(0, extra_wait_ms))

            html = await page.content()
            logger.info("✅ Страница загружена (%d символов)", len(html))
            return html

        except Exception as e:
            logger.error("❌ Ошибка загрузки %s: %s", url, e)
            raise
        finally:
            await context.close()
            await browser.close()
