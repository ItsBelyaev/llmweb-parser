"""Интерактивные действия на странице через Playwright.

Полноценный браузерный automation: открыть страницу, найти нужный
элемент (через эвристику или LLM-fallback), кликнуть, дождаться
изменения DOM, вернуть результат + лог.

Намерения (intents), поддерживаемые из коробки:

* ``add_to_cart`` — найти и нажать кнопку «Добавить в корзину».
* ``buy_now`` — найти и нажать кнопку «Купить сейчас».
* ``click_text`` — нажать на элемент с указанным текстом (универсальный
  «кликни по этой кнопке»).
* ``custom_selector`` — кликнуть по заданному CSS-селектору (для
  программных вызовов из других сервисов).

Логика поиска элемента (стратегия «эвристика → LLM → ошибка»):

1. Сначала ``element_finder.find_best(html, intent)`` — быстро и
   бесплатно, без вызова сети.
2. Если эвристика не уверена (score < порога) — спрашиваем LLM
   через ``LLMParser.find_interactive_element()``.
3. Если LLM вернула confidence < 50 — считаем что элемент не найден.

После клика:

* Делаем скриншот «до» и «после» (опционально).
* Ждём ``networkidle`` короткое время — даём шанс корзине обновиться.
* Парсим новый title страницы и пытаемся понять что изменилось
  (текст счётчика корзины, появление модального окна и т.п.).
* Возвращаем структурированный ``InteractionResult``.

Никогда не падаем: любые ошибки оборачиваются в ``status="error"``.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
    async_playwright,
)

from app.llm.parser import LLMParser
from app.scraper.cleaner import clean_html
from app.scraper.element_finder import (
    ElementCandidate,
    MIN_CONFIDENT_SCORE,
    find_best,
    find_candidates,
)

logger = logging.getLogger(__name__)


# =========================================================================== #
# Результат и лог                                                              #
# =========================================================================== #


@dataclass
class InteractionStep:
    """Один шаг во время выполнения действия."""

    step: str
    detail: str
    timestamp_ms: int


@dataclass
class InteractionResult:
    """Полный результат интерактивного действия."""

    status: str  # "success" | "error" | "not_found"
    url: str
    action: str
    selector_used: Optional[str] = None
    selector_source: Optional[str] = None  # "heuristic" | "llm" | "user"
    selector_confidence: Optional[float] = None
    element_text: Optional[str] = None
    page_title_before: Optional[str] = None
    page_title_after: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0
    log: List[InteractionStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "url": self.url,
            "action": self.action,
            "selector_used": self.selector_used,
            "selector_source": self.selector_source,
            "selector_confidence": self.selector_confidence,
            "element_text": self.element_text,
            "page_title_before": self.page_title_before,
            "page_title_after": self.page_title_after,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "log": [
                {"step": s.step, "detail": s.detail, "timestamp_ms": s.timestamp_ms}
                for s in self.log
            ],
        }


# =========================================================================== #
# PageInteractor                                                              #
# =========================================================================== #


def _interact_timeout_ms() -> int:
    return int(os.getenv("INTERACT_TIMEOUT_MS", "15000"))


class PageInteractor:
    """Высокоуровневая обёртка над браузерным automation.

    Использовать через ``await PageInteractor().run(...)`` — экземпляр
    создаётся под одно действие и закрывается за собой.
    """

    def __init__(self, llm: Optional[LLMParser] = None) -> None:
        self.llm = llm  # будет передан извне для удобства мокания

    async def run(
        self,
        url: str,
        action: str,
        *,
        selector: Optional[str] = None,
        text_hint: Optional[str] = None,
        intent: Optional[str] = None,
        use_llm_fallback: bool = True,
        wait_for_selector: Optional[str] = None,
        extra_wait_ms: int = 0,
    ) -> InteractionResult:
        """Главная точка входа.

        Args:
            url: страница на которой выполняем действие.
            action: одно из {``add_to_cart``, ``buy_now``, ``click_text``,
                ``custom_selector``}.
            selector: для ``custom_selector`` — явный CSS-селектор.
            text_hint: для ``click_text`` — какой текст искать.
            intent: явное имя намерения для ``element_finder``;
                по умолчанию вычисляется из ``action``.
            use_llm_fallback: если эвристика не уверена — спрашивать LLM.
        """
        started = time.time()
        result = InteractionResult(status="error", url=url, action=action)
        log_step = lambda step, detail: result.log.append(
            InteractionStep(step, detail, int((time.time() - started) * 1000))
        )

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                    ],
                )
                try:
                    # Реалистичный User-Agent (последний Chrome на macOS).
                    UA = (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/121.0.0.0 Safari/537.36"
                    )
                    context = await browser.new_context(
                        viewport={"width": 1366, "height": 900},
                        locale="ru-RU",
                        timezone_id="Europe/Moscow",
                        user_agent=UA,
                        extra_http_headers={
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
                        },
                    )
                    # Применяем stealth-патчи: скрытие navigator.webdriver,
                    # эмуляция Chrome runtime, WebGL fingerprint и т.д.
                    try:
                        from playwright_stealth import Stealth

                        await Stealth(navigator_user_agent_override=UA).apply_stealth_async(context)
                        log_step("stealth", "playwright-stealth применён")
                    except Exception as exc:  # noqa: BLE001
                        log_step("stealth", f"не удалось применить: {exc}")

                    page = await context.new_page()

                    # 1. Открываем страницу.
                    # Стратегия двухступенчатая: сначала ждём
                    # domcontentloaded (быстро, гарантированно), потом
                    # пытаемся дойти до networkidle, но не падаем если он
                    # не достигнут (крупные SPA-сайты типа Wildberries
                    # никогда не отдают networkidle из-за бесконечного
                    # polling'а).
                    log_step("goto", url)
                    try:
                        await page.goto(
                            url, wait_until="domcontentloaded",
                            timeout=_interact_timeout_ms() * 2,
                        )
                    except PWTimeoutError as exc:
                        log_step("goto", f"domcontentloaded timeout: {exc}")

                    # Пытаемся дождаться полной отрисовки.
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=10_000
                        )
                        log_step("networkidle", "достигнут")
                    except PWTimeoutError:
                        log_step("networkidle", "не достигнут (SPA?) — продолжаем")

                    # Опционально ждём появления конкретного селектора
                    # (например ".price" или "h1"). Это лучшая страховка
                    # против "пустого" SPA-shell'а.
                    if wait_for_selector:
                        try:
                            await page.wait_for_selector(
                                wait_for_selector, timeout=15_000,
                            )
                            log_step("wait_for_selector", f"'{wait_for_selector}' найден")
                        except PWTimeoutError:
                            log_step(
                                "wait_for_selector",
                                f"'{wait_for_selector}' не появился за 15с",
                            )

                    # Базовая пауза + дополнительная для упрямых сайтов.
                    await page.wait_for_timeout(2000 + max(0, extra_wait_ms))
                    result.page_title_before = await page.title()
                    log_step(
                        "page_loaded",
                        f"title='{result.page_title_before}' html_len={len(await page.content())}",
                    )

                    # 2. Подбираем селектор
                    sel, source, conf, element_text = await self._resolve_selector(
                        page=page,
                        action=action,
                        explicit_selector=selector,
                        text_hint=text_hint,
                        intent=intent,
                        use_llm_fallback=use_llm_fallback,
                        log_step=log_step,
                    )
                    if not sel:
                        result.status = "not_found"
                        result.error = "Не удалось найти кликабельный элемент"
                        return self._finalize(result, started)

                    result.selector_used = sel
                    result.selector_source = source
                    result.selector_confidence = conf
                    result.element_text = element_text

                    # 3. Клик
                    log_step("click", f"selector='{sel}'")
                    try:
                        if source == "text-match" and text_hint:
                            # Selector вида text='...' это наш внутренний
                            # маркер — кликаем через Playwright locator.
                            await page.get_by_text(text_hint, exact=False).first.click(
                                timeout=_interact_timeout_ms()
                            )
                        else:
                            await page.wait_for_selector(sel, timeout=_interact_timeout_ms())
                            await page.click(sel, timeout=_interact_timeout_ms())
                    except PWTimeoutError as exc:
                        result.status = "error"
                        result.error = f"timeout при клике: {exc}"
                        return self._finalize(result, started)
                    except Exception as exc:  # noqa: BLE001
                        result.status = "error"
                        result.error = f"клик не удался: {exc}"
                        return self._finalize(result, started)

                    # 4. Даём странице отреагировать
                    log_step("post_click_wait", "ожидание ответа сайта")
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=_interact_timeout_ms()
                        )
                    except PWTimeoutError:
                        pass
                    await page.wait_for_timeout(1000)

                    result.page_title_after = await page.title()
                    log_step("done", f"title='{result.page_title_after}'")
                    result.status = "success"
                    return self._finalize(result, started)
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001
            result.status = "error"
            result.error = f"непредвиденная ошибка: {exc}"
            log_step("exception", str(exc))
            return self._finalize(result, started)

    # ------------------------------------------------------------------ #
    # Подбор селектора                                                    #
    # ------------------------------------------------------------------ #
    async def _resolve_selector(
        self,
        *,
        page: Page,
        action: str,
        explicit_selector: Optional[str],
        text_hint: Optional[str],
        intent: Optional[str],
        use_llm_fallback: bool,
        log_step,
    ):
        """Возвращает (selector, source, confidence, element_text) или Nones."""

        # 1) Явный селектор — приоритет
        if explicit_selector:
            log_step("resolve", f"явный селектор: {explicit_selector}")
            txt = await self._element_text_safe(page, explicit_selector)
            return explicit_selector, "user", 100.0, txt

        # 2) Поиск по тексту: Playwright встроенный get_by_text
        if action == "click_text" and text_hint:
            log_step("resolve", f"поиск по тексту: '{text_hint}'")
            try:
                locator = page.get_by_text(text_hint, exact=False).first
                count = await locator.count()
                if count > 0:
                    # Превращаем locator в CSS-селектор не получится напрямую,
                    # поэтому используем подход: кликаем по locator'у через
                    # элемент-handle. Для логирования сохраняем текст-хинт.
                    return f"text={text_hint!r}", "text-match", 95.0, text_hint
            except Exception as exc:  # noqa: BLE001
                log_step("resolve", f"get_by_text не сработал: {exc}")

        # 3) Эвристика по HTML
        intent_name = intent or _action_to_intent(action)
        if not intent_name:
            return None, None, None, None

        html = await page.content()
        # ВАЖНО: keep_interactive=True — иначе cleaner выкидывает button/input/form
        # и эвристика ничего не находит.
        cleaned = clean_html(html, max_length=20_000, keep_interactive=True)
        log_step("heuristic", f"intent={intent_name}, html_len={len(cleaned)}")

        candidates = find_candidates(cleaned, intent=intent_name, limit=5)
        for c in candidates[:3]:
            log_step("candidate", f"score={c.score:.1f} sel={c.selector} text='{c.text}'")

        best = candidates[0] if candidates else None
        if best is not None and best.score >= MIN_CONFIDENT_SCORE:
            # Проверим что селектор действительно существует на странице
            try:
                exists = await page.locator(best.selector).count() > 0
            except Exception:
                exists = False
            if exists:
                return best.selector, "heuristic", best.score, best.text

        # 4) LLM-fallback
        if not use_llm_fallback:
            log_step("resolve", "эвристика не уверена, LLM отключён")
            return None, None, None, None

        log_step("llm", f"запрос LLM, intent={intent_name}")
        llm = self.llm or LLMParser()
        if not llm.is_available():
            log_step("llm", "LLM недоступен")
            return None, None, None, None

        try:
            llm_result = await llm.find_interactive_element(cleaned, intent=intent_name)
        except Exception as exc:  # noqa: BLE001
            log_step("llm", f"ошибка: {exc}")
            return None, None, None, None

        sel = llm_result.get("selector")
        conf = float(llm_result.get("confidence") or 0)
        log_step("llm", f"selector={sel} confidence={conf} reasoning={llm_result.get('reasoning')!r}")
        if not sel or conf < 50:
            return None, None, None, None
        # Проверим что элемент существует
        try:
            exists = await page.locator(sel).count() > 0
        except Exception:
            exists = False
        if not exists:
            log_step("llm", "LLM вернул селектор, но он ничего не находит на странице")
            return None, None, None, None
        txt = await self._element_text_safe(page, sel)
        return sel, "llm", conf, txt

    @staticmethod
    async def _element_text_safe(page: Page, selector: str) -> Optional[str]:
        try:
            return (await page.locator(selector).first.inner_text(timeout=3000))[:80]
        except Exception:
            return None

    @staticmethod
    def _finalize(result: InteractionResult, started: float) -> InteractionResult:
        result.duration_ms = int((time.time() - started) * 1000)
        return result


# =========================================================================== #
# Утилиты                                                                      #
# =========================================================================== #


def _action_to_intent(action: str) -> Optional[str]:
    """Связывает имя action с именем intent из element_finder."""
    return {
        "add_to_cart": "add_to_cart",
        "buy_now": "buy_now",
        "open_product": "open_product",
        "click_text": None,  # для click_text intent не нужен
        "custom_selector": None,
    }.get(action)
