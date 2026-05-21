"""LLM-парсер на базе LangChain с прямым fallback на HuggingFace API.

Дизайн:

* Поддерживаются два провайдера: HuggingFace (для разработки) и OpenAI
  (для production). Выбирается тот, чей ключ задан в окружении.
* Lazy init — LLM создаётся только при первом фактическом запросе, не
  при импорте модуля. Это нужно для CI: тесты могут подмокать парсер
  до того, как кто-то попытается подключиться к HF API.
* Fallback — если LangChain-цепочка падает (например, networkidle
  таймаут, странный JSON в ответе), мы вызываем HuggingFace API
  напрямую через ``huggingface_hub.InferenceClient``. Это позволяет
  пережить временные сбои langchain-huggingface.
* Все настройки берутся из переменных окружения (``HUGGINGFACE_MODEL``,
  ``HUGGINGFACE_TEMPERATURE`` и т.д.), что упрощает деплой.
"""

import json
import logging
import os
import re
from typing import Dict, Optional

from app.prompts.templates import (
    PRODUCT_EXTRACTION_TEMPLATE,
    SELECTORS_GENERATION_TEMPLATE,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Конфигурация моделей                                                        #
# --------------------------------------------------------------------------- #


def _hf_model() -> str:
    """Какую HF-модель использовать.

    По умолчанию — Llama-3.1-8B-Instruct. Это сильно лучше, чем 1B/3B,
    для задач извлечения структурированных данных из HTML.
    """
    return os.getenv("HUGGINGFACE_MODEL", "meta-llama/Llama-3.1-8B-Instruct")


def _hf_temperature() -> float:
    return float(os.getenv("HUGGINGFACE_TEMPERATURE", "0.0"))


def _hf_max_tokens() -> int:
    return int(os.getenv("HUGGINGFACE_MAX_TOKENS", "1024"))


# --------------------------------------------------------------------------- #
# Построение LangChain LLM                                                    #
# --------------------------------------------------------------------------- #


def _build_llm():
    """Создать LangChain LLM объект.

    Возвращает None, если ни один ключ не настроен или провайдер
    не отвечает. Никогда не бросает исключение — это удобно
    для запуска тестов с тестовым ключом-заглушкой.
    """
    openai_key = os.getenv("OPENAI_API_KEY", "")
    hf_key = os.getenv("HUGGINGFACE_API_KEY", "")

    if openai_key:
        try:
            from langchain_openai import ChatOpenAI

            logger.info("🔑 Используем OpenAI (GPT-4o-mini)")
            return ChatOpenAI(
                model="gpt-4o-mini",
                temperature=_hf_temperature(),
                max_tokens=_hf_max_tokens(),
                api_key=openai_key,
            )
        except ImportError:
            logger.warning("langchain-openai не установлен, пробуем HuggingFace")

    if hf_key:
        try:
            from langchain_huggingface import HuggingFaceEndpoint

            model = _hf_model()
            logger.info("🤗 Используем Hugging Face: %s", model)
            return HuggingFaceEndpoint(
                repo_id=model,
                huggingfacehub_api_token=hf_key,
                max_new_tokens=_hf_max_tokens(),
                temperature=max(_hf_temperature(), 0.01),  # HF не любит 0.0
            )
        except ImportError:
            logger.warning("langchain-huggingface не установлен")
        except Exception as exc:  # noqa: BLE001
            # Например, плохой ключ (тестовый stub) — не падаем, просто
            # вернёмся к fallback на прямой API-вызов.
            logger.warning("LangChain HF init failed: %s — fallback позже", exc)

    logger.warning("⚠️ Нет ключей API — LLM недоступен")
    return None


# --------------------------------------------------------------------------- #
# Извлечение JSON из ответа LLM                                                #
# --------------------------------------------------------------------------- #


def _extract_json(text: str) -> Optional[Dict]:
    """Толерантный JSON-парсер для ответов LLM.

    LLM любят:
    * заворачивать JSON в markdown-блоки ```json;
    * добавлять преамбулу типа "Вот результат:";
    * ставить trailing comma;
    * использовать одинарные кавычки вместо двойных;
    * писать ``None`` вместо ``null``.

    Эта функция всё это правит и пытается распарсить результат.
    """
    if not text:
        return None

    # Снимаем markdown-обёртки.
    cleaned = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*", "", cleaned)
    cleaned = cleaned.strip()

    # Сначала пытаемся как есть.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Ищем первый JSON-блок.
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None

    candidate = match.group(0)
    candidate = candidate.replace("'", '"')
    candidate = re.sub(r",\s*}", "}", candidate)
    candidate = re.sub(r",\s*]", "]", candidate)
    # Python-style None/True/False → JSON null/true/false
    candidate = re.sub(r"\bNone\b", "null", candidate)
    candidate = re.sub(r"\bTrue\b", "true", candidate)
    candidate = re.sub(r"\bFalse\b", "false", candidate)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        logger.warning("JSON decode error: %s | text: %s", exc, candidate[:200])
        return None


# --------------------------------------------------------------------------- #
# Прямой fallback вызов HuggingFace API                                        #
# --------------------------------------------------------------------------- #


def _direct_hf_call(prompt: str) -> Optional[str]:
    """Прямой вызов HuggingFace Inference API в обход LangChain.

    Используется когда LangChain-цепочка падает (бывает регулярно
    из-за их частых breaking changes), а сам HF API при этом работает.
    """
    hf_key = os.getenv("HUGGINGFACE_API_KEY", "")
    if not hf_key:
        return None
    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(token=hf_key)
        response = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=_hf_model(),
            max_tokens=_hf_max_tokens(),
            temperature=_hf_temperature(),
        )
        return response.choices[0].message.content
    except Exception as exc:  # noqa: BLE001
        logger.error("Ошибка прямого HF-вызова: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Парсер                                                                       #
# --------------------------------------------------------------------------- #


class LLMParser:
    """Парсер на базе LangChain + HuggingFace.

    Lazy initialization: LLM и chain создаются только при первом
    реальном вызове, не при создании объекта. Это нужно для тестов:
    можно создать ``LLMParser()`` с тестовым ключом и подмокать
    ``_chain`` / ``_direct_hf_call`` через ``unittest.mock``.
    """

    def __init__(self):
        # Lazy — не строим LLM до первого фактического вызова.
        self._llm = None
        self._chain = None
        self._selector_chain = None
        self._initialized = False

    # ------------------------------------------------------------------ #
    def _ensure_init(self) -> None:
        # ``getattr`` чтобы быть терпимым к тестам, создающим объект
        # через ``LLMParser.__new__(LLMParser)`` и подмонтированию полей
        # вручную — там ``_initialized`` отсутствует, и это OK.
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        # Если тест уже выставил _llm/_chain вручную — не перетираем.
        if getattr(self, "_llm", None) is None:
            self._llm = _build_llm()
        if getattr(self, "_chain", None) is None and self._llm is not None:
            self._chain = PRODUCT_EXTRACTION_TEMPLATE | self._llm
        if getattr(self, "_selector_chain", None) is None and self._llm is not None:
            self._selector_chain = SELECTORS_GENERATION_TEMPLATE | self._llm

    def is_available(self) -> bool:
        """Доступен ли хотя бы один способ обратиться к LLM."""
        self._ensure_init()
        # Даже если LangChain не построился, fallback может работать,
        # если есть HF-ключ.
        return self._chain is not None or bool(os.getenv("HUGGINGFACE_API_KEY"))

    # ------------------------------------------------------------------ #
    # Прямое извлечение                                                   #
    # ------------------------------------------------------------------ #
    async def extract_product_data(self, html: str, source: str = "other") -> Dict:
        """Извлекает данные о товаре из HTML.

        Возвращает словарь:
            {"title": ..., "price": ..., "article": ..., "image_url": ...,
             "_raw": <raw LLM response>}
        """
        self._ensure_init()
        logger.info("🤖 LLM-парсинг (source=%s, html_len=%d)", source, len(html))

        raw_text = await self._call_llm(
            chain=self._chain,
            template=PRODUCT_EXTRACTION_TEMPLATE,
            vars={"source": source, "html": html},
        )

        if raw_text is None:
            return self._empty_result()

        data = _extract_json(raw_text)
        if data is None:
            logger.warning("Не удалось извлечь JSON: %s", raw_text[:300])
            return self._empty_result()

        result = self._normalize(data)
        result["_raw"] = raw_text
        logger.info(
            "✅ Извлечено: title=%s price=%s article=%s",
            result.get("title"),
            result.get("price"),
            result.get("article"),
        )
        return result

    # ------------------------------------------------------------------ #
    # Генерация CSS-селекторов                                            #
    # ------------------------------------------------------------------ #
    async def generate_selectors(self, html: str) -> Dict:
        """Просит LLM вернуть CSS-селекторы для основных полей товара.

        Возвращает словарь полей: ``name``, ``price``, ``currency``,
        ``pictures``, ``category``, ``article``. Любое поле может быть
        None, если LLM не смогла его найти.
        """
        self._ensure_init()
        logger.info("🎯 Генерация селекторов (html_len=%d)", len(html))

        raw_text = await self._call_llm(
            chain=self._selector_chain,
            template=SELECTORS_GENERATION_TEMPLATE,
            vars={"html": html},
        )
        if raw_text is None:
            return {}

        data = _extract_json(raw_text)
        if not isinstance(data, dict):
            logger.warning("LLM вернул некорректные селекторы: %s", raw_text[:300])
            return {}
        # Берём только известные поля и нормализуем пустые значения.
        out: Dict[str, Optional[str]] = {}
        for k in ("name", "price", "currency", "pictures", "category", "article"):
            v = data.get(k)
            if isinstance(v, str):
                v = v.strip()
                out[k] = v or None
            else:
                out[k] = None
        out["_raw"] = raw_text
        return out

    # ------------------------------------------------------------------ #
    # Общий вызов LLM с fallback                                          #
    # ------------------------------------------------------------------ #
    async def _call_llm(self, chain, template, vars: Dict) -> Optional[str]:
        raw_text: Optional[str] = None

        # 1) LangChain-цепочка
        if chain is not None:
            try:
                result = await chain.ainvoke(vars)
                raw_text = result.content if hasattr(result, "content") else str(result)
                logger.info("✅ LangChain ответ получен (%d символов)", len(raw_text))
            except Exception as exc:  # noqa: BLE001
                logger.warning("❌ LangChain не справился: %s — fallback", exc)

        # 2) Прямой HF API
        if raw_text is None:
            prompt = template.format(**vars)
            logger.info("🔄 Fallback: прямой HF API")
            raw_text = _direct_hf_call(prompt)

        return raw_text

    # ------------------------------------------------------------------ #
    # Нормализация                                                        #
    # ------------------------------------------------------------------ #
    def _normalize(self, data: Dict) -> Dict:
        """Приводит словарь LLM-ответа к нормализованному виду.

        Учитывает альтернативные имена полей (``name`` вместо ``title``,
        ``sku`` вместо ``article``) и парсит цену из строки.
        """
        title = data.get("title") or data.get("name") or data.get("product_name")
        price = data.get("price") or data.get("cost")
        article = data.get("article") or data.get("sku") or data.get("id")
        image_url = (
            data.get("image_url")
            or data.get("image")
            or data.get("img")
            or data.get("photo")
        )

        if price is not None:
            try:
                price_str = (
                    str(price)
                    .replace(",", ".")
                    .replace(" ", "")
                    .replace(" ", "")
                    .replace("₽", "")
                )
                match = re.search(r"\d+\.?\d*", price_str)
                price = float(match.group()) if match else None
            except Exception:  # noqa: BLE001
                price = None

        return {
            "title": str(title).strip() if title else None,
            "price": price,
            "article": str(article).strip() if article else None,
            "image_url": str(image_url).strip() if image_url else None,
        }

    def _empty_result(self) -> Dict:
        return {"title": None, "price": None, "article": None, "image_url": None, "_raw": None}
