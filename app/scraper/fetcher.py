"""
Загрузка веб-страниц через Playwright (headless Chromium)
"""

import logging
import random
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    reraise=True,
)
async def fetch_page(url: str, timeout: int = 60_000) -> str:
    """
    Загружает страницу через Playwright.
    Повторяет попытку при ошибке (до 3 раз с экспоненциальной задержкой).
    """
    from playwright.async_api import async_playwright

    logger.info("🌐 Загрузка страницы: %s", url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
        )
        try:
            page = await context.new_page()

            # Скрываем webdriver
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            await page.goto(url, wait_until="networkidle", timeout=timeout)
            await page.wait_for_timeout(2000)

            html = await page.content()
            logger.info("✅ Страница загружена (%d символов)", len(html))
            return html

        except Exception as e:
            logger.error("❌ Ошибка загрузки %s: %s", url, e)
            raise
        finally:
            await context.close()
            await browser.close()
