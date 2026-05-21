"""
API-маршруты FastAPI.

Делится на четыре группы:

1. **Парсинг** — ``/parse``, ``/results``, ``/results/{id}``.
2. **Действия пользователя** — ``/interactions``.
3. **Утилиты** — ``DELETE /results``.
4. **CSS-селекторы (Parsmart)** — ``/selectors/generate``,
   ``/selectors/apply``, ``/selectors``, ``/selectors/{domain}``,
   ``/selectors/export.toon``.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.database.db import (
    save_parse_result,
    get_parse_results,
    get_parse_result_by_id,
    count_parse_results,
    save_interaction,
    get_interactions,
    count_interactions,
    save_domain_markup,
    get_domain_markup,
    list_domain_markups,
    delete_domain_markup,
    save_interaction_run,
    get_interaction_runs,
    get_interaction_run_by_id,
    count_interaction_runs,
)
from app.llm.parser import LLMParser
from app.models.schemas import (
    DomainMarkup,
    DomainMarkupList,
    InteractionCreate,
    InteractionList,
    InteractionLogStep,
    InteractionResponse,
    InteractRequest,
    InteractResponse,
    InteractResponseList,
    ParseRequest,
    ParseResponse,
    ParseResponseList,
    ProductData,
    ProductPageSelectors,
    SelectorsApplyRequest,
    SelectorsGenerateRequest,
    SelectorsGenerateResponse,
)
from app.scraper.applier import apply_selectors, origin, validate_selectors
from app.scraper.cleaner import clean_html
from app.scraper.fetcher import fetch_page
from app.scraper.interactor import PageInteractor
from app.toon.serializer import dump_domains

logger = logging.getLogger(__name__)
router = APIRouter()

# Один экземпляр парсера на всё приложение
_llm_parser = LLMParser()


# ─── Парсинг ─────────────────────────────────────────────────────────────────


@router.post("/parse", response_model=ParseResponse, summary="Запустить парсинг страницы")
async def parse_product(req: ParseRequest):
    """
    Принимает URL товарной страницы, загружает её через Playwright,
    очищает HTML и извлекает данные через LLM (LangChain).
    Результат сохраняется в SQLite.
    """
    logger.info("📥 Запрос парсинга: source=%s url=%s", req.source, req.url)
    await save_interaction("parse_request", req.url)

    status = "success"
    error_msg: Optional[str] = None
    product: Optional[ProductData] = None
    raw_response: Optional[str] = None

    try:
        # 1. Загрузка страницы (с поддержкой extra_wait_ms / wait_for_selector
        # для медленных SPA-маркетплейсов).
        html = await fetch_page(
            req.url,
            wait_for_selector=req.wait_for_selector,
            extra_wait_ms=req.extra_wait_ms,
        )

        # 2. Очистка HTML
        cleaned_html = clean_html(html)

        # 3. LLM-извлечение
        if not _llm_parser.is_available():
            raise RuntimeError(
                "LLM недоступен — добавьте HUGGINGFACE_API_KEY или OPENAI_API_KEY в .env"
            )

        data = await _llm_parser.extract_product_data(cleaned_html, req.source)
        raw_response = data.pop("_raw", None)

        # 4. Валидация через Pydantic
        try:
            product = ProductData(**data)
        except Exception as val_err:
            logger.warning("⚠️ Ошибка валидации ProductData: %s", val_err)
            # Создаём с тем что есть, без падения
            product = ProductData(
                title=data.get("title"),
                price=None,
                article=data.get("article"),
                image_url=None,
            )

    except Exception as e:
        logger.error("❌ Ошибка парсинга %s: %s", req.url, e)
        status = "error"
        error_msg = str(e)

    # 5. Сохранение в БД
    record_id = await save_parse_result(
        url=req.url,
        source=req.source,
        status=status,
        error=error_msg,
        title=product.title if product else None,
        price=product.price if product else None,
        article=product.article if product else None,
        image_url=product.image_url if product else None,
        raw_response=raw_response,
    )

    return ParseResponse(
        id=record_id,
        url=req.url,
        source=req.source,
        status=status,
        error=error_msg,
        product=product,
        raw_response=raw_response,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/results", response_model=ParseResponseList, summary="История результатов парсинга")
async def get_results(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Возвращает историю всех запросов парсинга из базы данных."""
    rows = await get_parse_results(limit=limit, offset=offset)
    total = await count_parse_results()

    results = []
    for r in rows:
        product = None
        if r.get("title") or r.get("price") or r.get("article") or r.get("image_url"):
            try:
                product = ProductData(
                    title=r.get("title"),
                    price=r.get("price"),
                    article=r.get("article"),
                    image_url=r.get("image_url"),
                )
            except Exception:
                pass

        results.append(
            ParseResponse(
                id=r["id"],
                url=r["url"],
                source=r.get("source", "other"),
                status=r.get("status", "success"),
                error=r.get("error"),
                product=product,
                raw_response=r.get("raw_response"),
                timestamp=datetime.fromisoformat(r["created_at"]),
            )
        )

    return ParseResponseList(results=results, total=total)


@router.get("/results/{record_id}", response_model=ParseResponse, summary="Получить результат по ID")
async def get_result(record_id: int):
    """Возвращает конкретную запись результата парсинга по её ID."""
    row = await get_parse_result_by_id(record_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Запись {record_id} не найдена")

    product = None
    if row.get("title") or row.get("price"):
        try:
            product = ProductData(
                title=row.get("title"),
                price=row.get("price"),
                article=row.get("article"),
                image_url=row.get("image_url"),
            )
        except Exception:
            pass

    return ParseResponse(
        id=row["id"],
        url=row["url"],
        source=row.get("source", "other"),
        status=row.get("status", "success"),
        error=row.get("error"),
        product=product,
        raw_response=row.get("raw_response"),
        timestamp=datetime.fromisoformat(row["created_at"]),
    )


# ─── Действия пользователя ───────────────────────────────────────────────────


@router.post("/interactions", response_model=InteractionResponse, summary="Записать действие")
async def create_interaction(body: InteractionCreate):
    """Сохраняет пользовательское действие (клик, просмотр и т.д.)."""
    rec_id = await save_interaction(body.action, body.payload)
    return InteractionResponse(
        id=rec_id,
        action=body.action,
        payload=body.payload,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/interactions", response_model=InteractionList, summary="История действий")
async def list_interactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Возвращает историю действий пользователя."""
    rows = await get_interactions(limit=limit, offset=offset)
    total = await count_interactions()
    items = [
        InteractionResponse(
            id=r["id"],
            action=r["action"],
            payload=r.get("payload"),
            timestamp=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]
    return InteractionList(interactions=items, total=total)


# ─── Утилиты ─────────────────────────────────────────────────────────────────


@router.delete("/results", summary="Очистить историю парсинга")
async def clear_results():
    """Удаляет все записи из таблицы parsed_products."""
    import aiosqlite
    from app.database.db import _db_path

    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM parsed_products")
        await db.commit()
    await save_interaction("clear_results")
    return {"status": "success", "message": "История очищена"}


# ─── CSS-селекторы (режим Parsmart) ─────────────────────────────────────────


@router.post(
    "/selectors/generate",
    response_model=SelectorsGenerateResponse,
    summary="Сгенерировать CSS-селекторы для домена",
)
async def selectors_generate(req: SelectorsGenerateRequest):
    """Просит LLM проанализировать опорную страницу товара и вернуть
    CSS-селекторы для основных полей. Невалидные/ничего-не-нашедшие
    селекторы автоматически отфильтровываются. Результат сохраняется
    в таблицу ``domain_markups`` (UPSERT по домену).
    """
    logger.info("POST /selectors/generate url=%s device=%s", req.url, req.device)
    await save_interaction("selectors_generate", req.url)

    try:
        html = await fetch_page(
            req.url,
            wait_for_selector=req.wait_for_selector,
            extra_wait_ms=req.extra_wait_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("fetch failed: %s", exc)
        return SelectorsGenerateResponse(status="error", error=f"загрузка страницы: {exc}")

    cleaned = clean_html(html)
    if not _llm_parser.is_available():
        return SelectorsGenerateResponse(
            status="error",
            error="LLM недоступен — добавьте HUGGINGFACE_API_KEY или OPENAI_API_KEY в .env",
        )

    raw = await _llm_parser.generate_selectors(cleaned)
    raw.pop("_raw", None)
    if not raw:
        return SelectorsGenerateResponse(
            status="error", error="LLM не вернул валидный набор селекторов"
        )

    # Валидация + автокоррекция мусорных селекторов.
    validated, comments = validate_selectors(raw, html)
    markup = DomainMarkup(
        mainPageUrl=origin(req.url),
        device=req.device,
        markdownComments=comments,
        productPage=validated,
    )
    await save_domain_markup(markup.mainPageUrl, markup.model_dump(mode="json"))
    return SelectorsGenerateResponse(status="success", domain=markup)


@router.post(
    "/selectors/apply",
    response_model=ParseResponse,
    summary="Применить сохранённые селекторы к новому URL (без LLM)",
)
async def selectors_apply(req: SelectorsApplyRequest):
    """Применяет уже сгенерированные селекторы к URL того же домена.

    Работает по чистому BeautifulSoup — LLM не вызывается, операция
    моментальная и бесплатная. Если для домена нет сохранённой
    разметки, возвращается 404.
    """
    logger.info("POST /selectors/apply url=%s", req.url)
    markup_dict = await get_domain_markup(req.url)
    if markup_dict is None:
        raise HTTPException(
            status_code=404,
            detail="Для этого домена нет сохранённой разметки. "
            "Сначала вызовите POST /api/selectors/generate.",
        )
    markup = DomainMarkup(**markup_dict)

    try:
        html = await fetch_page(
            req.url,
            wait_for_selector=req.wait_for_selector,
            extra_wait_ms=req.extra_wait_ms,
        )
    except Exception as exc:  # noqa: BLE001
        record_id = await save_parse_result(
            url=req.url, source="selectors", status="error", error=str(exc)
        )
        return ParseResponse(
            id=record_id,
            url=req.url,
            source="selectors",
            status="error",
            error=str(exc),
            product=None,
            timestamp=datetime.now(timezone.utc),
        )

    extracted = apply_selectors(html, markup.productPage, base_url=req.url)
    try:
        product = ProductData(
            title=extracted.get("title"),
            price=extracted.get("price"),
            article=extracted.get("article"),
            image_url=extracted.get("image_url"),
        )
    except Exception as exc:  # noqa: BLE001 — Pydantic validation
        product = ProductData(title=extracted.get("title"))
        logger.warning("Не удалось пройти валидацию: %s", exc)

    record_id = await save_parse_result(
        url=req.url,
        source="selectors",
        status="success",
        title=product.title,
        price=product.price,
        article=product.article,
        image_url=product.image_url,
    )
    return ParseResponse(
        id=record_id,
        url=req.url,
        source="selectors",
        status="success",
        product=product,
        timestamp=datetime.now(timezone.utc),
    )


@router.get(
    "/selectors",
    response_model=DomainMarkupList,
    summary="Список всех сохранённых разметок доменов",
)
async def selectors_list():
    rows = await list_domain_markups()
    items = [DomainMarkup(**r) for r in rows]
    return DomainMarkupList(domains=items, total=len(items))


@router.get(
    "/selectors/export.toon",
    response_class=PlainTextResponse,
    summary="Скачать все разметки в формате TOON",
)
async def selectors_export_toon():
    rows = await list_domain_markups()
    items = [DomainMarkup(**r) for r in rows]
    return PlainTextResponse(content=dump_domains(items))


@router.get(
    "/selectors/{domain}",
    response_model=DomainMarkup,
    summary="Получить разметку конкретного домена",
)
async def selectors_get(domain: str):
    markup_dict = await get_domain_markup(domain)
    if markup_dict is None:
        raise HTTPException(status_code=404, detail="Разметка не найдена")
    return DomainMarkup(**markup_dict)


@router.delete(
    "/selectors/{domain}",
    summary="Удалить разметку конкретного домена",
)
async def selectors_delete(domain: str):
    ok = await delete_domain_markup(domain)
    if not ok:
        raise HTTPException(status_code=404, detail="Разметка не найдена")
    return {"status": "success"}


# ─── Интерактивные действия (ТЗ май 2026) ────────────────────────────────────


@router.post(
    "/interact",
    response_model=InteractResponse,
    summary="Универсальное интерактивное действие на странице",
)
async def interact(req: InteractRequest):
    """Открывает страницу через Playwright и выполняет интерактивное действие.

    Поддерживаемые действия (поле ``action``):

    * ``add_to_cart`` — найти и нажать кнопку «Добавить в корзину».
    * ``buy_now`` — кнопка «Купить сейчас» / Checkout.
    * ``open_product`` — открыть карточку товара.
    * ``click_text`` — кликнуть по элементу с текстом из ``text_hint``.
    * ``custom_selector`` — кликнуть по явному CSS-селектору из ``selector``.

    Стратегия поиска:
        1. Эвристический scoring (быстро, бесплатно).
        2. Если score < порога — LLM-fallback (если ``use_llm_fallback=true``).
        3. Если оба не дали уверенного результата — ``status=not_found``.

    Никогда не возвращает 5xx из-за ошибок взаимодействия — все ошибки
    оборачиваются в ``status="error"`` с диагностическим логом.
    """
    logger.info("POST /interact url=%s action=%s", req.url, req.action)
    await save_interaction("interact_request", f"{req.action}:{req.url}")

    interactor = PageInteractor(llm=_llm_parser)
    result = await interactor.run(
        url=req.url,
        action=req.action,
        selector=req.selector,
        text_hint=req.text_hint,
        intent=req.intent,
        use_llm_fallback=req.use_llm_fallback,
        wait_for_selector=req.wait_for_selector,
        extra_wait_ms=req.extra_wait_ms,
    )

    record_id = await save_interaction_run(
        url=result.url,
        action=result.action,
        status=result.status,
        selector_used=result.selector_used,
        selector_source=result.selector_source,
        selector_confidence=result.selector_confidence,
        element_text=result.element_text,
        page_title_before=result.page_title_before,
        page_title_after=result.page_title_after,
        error=result.error,
        duration_ms=result.duration_ms,
        log=[s.__dict__ for s in result.log],
    )

    return InteractResponse(
        id=record_id,
        status=result.status,
        url=result.url,
        action=result.action,
        selector_used=result.selector_used,
        selector_source=result.selector_source,
        selector_confidence=result.selector_confidence,
        element_text=result.element_text,
        page_title_before=result.page_title_before,
        page_title_after=result.page_title_after,
        error=result.error,
        duration_ms=result.duration_ms,
        log=[
            InteractionLogStep(step=s.step, detail=s.detail, timestamp_ms=s.timestamp_ms)
            for s in result.log
        ],
        timestamp=datetime.now(timezone.utc),
    )


@router.post(
    "/cart/add",
    response_model=InteractResponse,
    summary="Шорткат: добавить товар в корзину",
)
async def cart_add(req: SelectorsApplyRequest):
    """Удобный шорткат для самого популярного сценария.

    Эквивалентно POST /api/interact с action=add_to_cart.
    """
    request = InteractRequest(url=req.url, action="add_to_cart")
    return await interact(request)


@router.get(
    "/interactions/runs",
    response_model=InteractResponseList,
    summary="История интерактивных действий",
)
async def list_interaction_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows = await get_interaction_runs(limit=limit, offset=offset)
    total = await count_interaction_runs()
    items: list[InteractResponse] = []
    for r in rows:
        items.append(
            InteractResponse(
                id=r["id"],
                status=r["status"],
                url=r["url"],
                action=r["action"],
                selector_used=r.get("selector_used"),
                selector_source=r.get("selector_source"),
                selector_confidence=r.get("selector_confidence"),
                element_text=r.get("element_text"),
                page_title_before=r.get("page_title_before"),
                page_title_after=r.get("page_title_after"),
                error=r.get("error"),
                duration_ms=r.get("duration_ms") or 0,
                log=[
                    InteractionLogStep(**s) if isinstance(s, dict) else s
                    for s in (r.get("log") or [])
                ],
                timestamp=datetime.fromisoformat(r["created_at"]),
            )
        )
    return InteractResponseList(interactions=items, total=total)


@router.get(
    "/interactions/runs/{record_id}",
    response_model=InteractResponse,
    summary="Детали конкретного действия",
)
async def get_interaction_run(record_id: int):
    r = await get_interaction_run_by_id(record_id)
    if not r:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return InteractResponse(
        id=r["id"],
        status=r["status"],
        url=r["url"],
        action=r["action"],
        selector_used=r.get("selector_used"),
        selector_source=r.get("selector_source"),
        selector_confidence=r.get("selector_confidence"),
        element_text=r.get("element_text"),
        page_title_before=r.get("page_title_before"),
        page_title_after=r.get("page_title_after"),
        error=r.get("error"),
        duration_ms=r.get("duration_ms") or 0,
        log=[
            InteractionLogStep(**s) if isinstance(s, dict) else s
            for s in (r.get("log") or [])
        ],
        timestamp=datetime.fromisoformat(r["created_at"]),
    )
