from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from openpyxl import load_workbook

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
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from metric_pulse import main as main_module  # noqa: E402
from metric_pulse import processor as processor_module  # noqa: E402
from metric_pulse.collector import CollectionResult, EvidenceItem  # noqa: E402
from metric_pulse.db import Base, SessionLocal, engine  # noqa: E402
from metric_pulse.main import app  # noqa: E402
from metric_pulse.security import bootstrap_admin  # noqa: E402
from metric_pulse.workbook import find_header_row, make_unique_headers  # noqa: E402


class FixtureWorkbookCollector:
    """Test-only pipeline double. Production code never imports the expected workbook."""

    def __init__(self) -> None:
        self.workbook = load_workbook(GOLD_WORKBOOK, read_only=True, data_only=False)
        self.columns: dict[str, dict[str, int]] = {}
        for sheet in self.workbook.worksheets:
            header_row = find_header_row(sheet)
            headers = make_unique_headers(
                [sheet.cell(header_row, column).value for column in range(1, sheet.max_column + 1)]
            )
            self.columns[sheet.title] = {
                header: index for index, header in enumerate(headers, start=1)
            }

    async def collect(self, record, unit) -> CollectionResult:
        sheet = self.workbook[record.sheet_name]
        columns = self.columns[record.sheet_name]
        values = {
            field: sheet.cell(record.source_row, columns[field]).value
            for field in unit.target_fields
        }
        return CollectionResult(
            values=values,
            evidence=[
                EvidenceItem(
                    title="External fixture adapter",
                    locator=f"{record.sheet_name}!row:{record.source_row}",
                    metadata={"provider": "test-fixture"},
                )
            ],
            validation={"valid": True, "fixture": True},
            model="test-fixture",
        )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    async def model_ready(_self) -> dict[str, object]:
        return {"ok": True, "models": []}

    monkeypatch.setattr(main_module.OMLXClient, "health", model_ready)
    monkeypatch.setattr(processor_module, "configured_collector", FixtureWorkbookCollector)
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
