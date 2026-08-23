from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest

TEST_ROOT = Path("/private/tmp/metric-pulse-platform-tests")
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
EMPTY_WORKBOOK = WORKSPACE_ROOT / "人工智能-空表--250224-V1.xlsx"
GOLD_WORKBOOK = WORKSPACE_ROOT / "人工智能-修复表--250224-V1.xlsx"

os.environ.update(
    {
        "MP_ENV": "test",
        "MP_DATABASE_URL": f"sqlite:///{TEST_ROOT / 'test.db'}",
        "MP_OBJECT_ROOT": str(TEST_ROOT / "objects"),
        "MP_EXPORT_ROOT": str(TEST_ROOT / "exports"),
        "MP_BOOTSTRAP_USERNAME": "admin",
        "MP_BOOTSTRAP_PASSWORD": "test-password",
        "MP_VISION_ANALYSIS_ENABLED": "false",
        "MP_EAGER_TASKS": "true",
        "MP_COLLECTOR_MODE": "gold",
        "MP_GOLD_WORKBOOK_PATH": str(GOLD_WORKBOOK),
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from metric_pulse.db import Base, SessionLocal, engine  # noqa: E402
from metric_pulse.main import app  # noqa: E402
from metric_pulse.security import bootstrap_admin  # noqa: E402


@pytest.fixture()
def client() -> Generator[TestClient]:
    engine.dispose()
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)
    TEST_ROOT.mkdir(parents=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bootstrap_admin(db)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "test-password"},
        )
        assert response.status_code == 200, response.text
        yield test_client
