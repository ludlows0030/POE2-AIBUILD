"""Pytest configuration — loads .env before tests run."""
from pathlib import Path

import pytest
from dotenv import load_dotenv

# .env is in project root (3 levels up from tests/conftest.py)
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
