"""
Конфигурация pytest: фикстуры, настройки тестовой БД.
"""

import os
import asyncio
import tempfile
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# aiosqlite открывает новое соединение при каждом вызове —
# :memory: теряет схему между вызовами, поэтому используем реальный файл.
_tmp_db = tempfile.NamedTemporaryFile(suffix="_api_test.db", delete=False)
_tmp_db.close()
os.environ["DB_PATH"] = _tmp_db.name
os.environ["HUGGINGFACE_API_KEY"] = "test_key_stub"

# Импортируем ПОСЛЕ установки переменных окружения
from app.main import app
from app.database.db import init_db


@pytest.fixture(scope="session")
def event_loop():
    """Единый event loop для всей сессии тестов"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_init():
    """Инициализация схемы тестовой БД один раз за сессию"""
    await init_db()


@pytest_asyncio.fixture
async def client(db_init):
    """HTTP-клиент для тестирования FastAPI приложения"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
