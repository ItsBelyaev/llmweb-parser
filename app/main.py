"""
LLM Web Parser — FastAPI приложение.

Точка входа: ``uvicorn app.main:app`` или ``python run.py``.

Что делает приложение:
  * рендерит дашборд на ``/``;
  * предоставляет REST API под префиксом ``/api`` (см. ``app/api/routes.py``);
  * автогенерирует Swagger UI на ``/docs`` и ReDoc на ``/redoc``;
  * при старте инициализирует схему SQLite (``init_db``).
"""

import logging
import os
from contextlib import asynccontextmanager

# Подтягиваем переменные окружения из .env до импорта роутов (которые
# создают LLMParser и читают HUGGINGFACE_API_KEY на этапе init).
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.database.db import init_db
from app.utils.logger import setup_logger

setup_logger()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Запуск LLM Web Parser...")
    await init_db()
    logger.info("✅ База данных инициализирована")
    yield
    logger.info("🛑 Остановка приложения")


app = FastAPI(
    title="LLM Web Parser",
    description=(
        "AI-сервис автоматизированного извлечения данных с сайтов с помощью LLM и LangChain.\n\n"
        "**Документация API**: /docs | **ReDoc**: /redoc"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# Монтируем статику
_base_dir = os.path.dirname(os.path.dirname(__file__))
_static_dir = os.path.join(_base_dir, "static")
if os.path.exists(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    template_path = os.path.join(_base_dir, "templates", "dashboard.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(
        content="<h1>LLM Web Parser</h1><p><a href='/docs'>📄 API Docs (Swagger)</a></p>"
    )


@app.get("/health", tags=["service"])
async def health_check():
    return {"status": "ok", "service": "LLM Web Parser", "version": "2.0.0"}
