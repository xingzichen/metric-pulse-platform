from __future__ import annotations

import asyncio

from openpyxl import load_workbook

from metric_pulse import main as main_module
from tests.conftest import EMPTY_WORKBOOK, GOLD_WORKBOOK, TEST_ROOT


def test_upload_collect_review_and_export_real_fixture(client) -> None:
    with EMPTY_WORKBOOK.open("rb") as handle:
        response = client.post(
            "/api/v1/files",
            files={
                "upload": (
                    EMPTY_WORKBOOK.name,
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert response.status_code == 201, response.text
    file_payload = response.json()
    assert file_payload["analysis"]["sheet_count"] == 11
    target_sheet = next(
        sheet for sheet in file_payload["analysis"]["sheets"] if sheet["name"].startswith("人工智能算法收藏")
    )

    response = client.post(
        "/api/v1/tasks",
        json={
            "file_id": file_payload["id"],
            "name": "real-fixture-snapshot",
            "datasets": [
                {
                    "sheet_name": target_sheet["name"],
                    "descriptor_fields": ["collect_date", "rank"],
                    "target_fields": ["name", "star", "star_unit", "source_url"],
                    "business_key_fields": ["logic_id"],
                    "mode": "snapshot_build",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    # The production planner sees only the uploaded template. Expected-workbook
    # rows are deliberately unavailable and therefore cannot expand this task.
    assert task["stats"]["total"] == 1

    response = client.post(
        f"/api/v1/tasks/{task['id']}/start",
        json={"expected_version": task["version"]},
    )
    assert response.status_code == 202, response.text
    task = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert task["status"] == "SUCCEEDED"
    assert task["stats"]["succeeded"] == task["stats"]["total"]

    units = []
    offset = 0
    while True:
        page = client.get(
            f"/api/v1/tasks/{task['id']}/review-queue",
            params={"offset": offset, "limit": 200},
        ).json()
        units.extend(page["items"])
        offset += len(page["items"])
        if offset >= page["total"]:
            break
    for start in range(0, len(units), 200):
        preview = client.post(
            f"/api/v1/tasks/{task['id']}/reviews/bulk/preview",
            json={"unit_ids": [item["id"] for item in units[start : start + 200]]},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["eligible"] == len(units[start : start + 200])
        response = client.post(
            f"/api/v1/tasks/{task['id']}/reviews/bulk/commit",
            json={"preview_token": preview.json()["previewToken"]},
        )
        assert response.status_code == 200, response.text

    readiness = client.get(f"/api/v1/tasks/{task['id']}/export-readiness").json()
    assert readiness == {"ready": True, "blockers": [], "counts": {"APPROVED": len(units)}}
    response = client.post(f"/api/v1/tasks/{task['id']}/exports")
    assert response.status_code == 201, response.text
    export = response.json()
    assert export["status"] == "READY"
    download = client.get(f"/api/v1/exports/{export['id']}/download")
    assert download.status_code == 200

    source = load_workbook(GOLD_WORKBOOK, read_only=True, data_only=False)
    actual_path = TEST_ROOT / "api-acceptance-output.xlsx"
    actual_path.parent.mkdir(parents=True, exist_ok=True)
    actual_path.write_bytes(download.content)
    actual = load_workbook(actual_path, read_only=True, data_only=False)
    for row in (3,):
        for column in (4, 5, 6, 10):
            assert (
                actual[target_sheet["name"]].cell(row, column).value
                == source[target_sheet["name"]].cell(row, column).value
            )
    actual.close()
    source.close()


def test_vision_preflight_failure_is_persisted_for_every_sheet(client, monkeypatch) -> None:
    async def unavailable(_self):
        raise RuntimeError("provider rejected credentials")

    monkeypatch.setattr(main_module.settings, "vision_analysis_enabled", False)
    with EMPTY_WORKBOOK.open("rb") as handle:
        response = client.post(
            "/api/v1/files",
            files={
                "upload": (
                    EMPTY_WORKBOOK.name,
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert response.status_code == 201, response.text
    file_id = response.json()["id"]

    monkeypatch.setattr(main_module.settings, "vision_analysis_enabled", True)
    monkeypatch.setattr(main_module.OMLXClient, "health", unavailable)
    asyncio.run(main_module.vision_recognize_file(file_id))

    detail = client.get(f"/api/v1/files/{file_id}")
    assert detail.status_code == 200, detail.text
    sheets = detail.json()["analysis"]["sheets"]
    assert sheets
    assert all(sheet["vision"]["valid"] is False for sheet in sheets)
    assert all("OMLX preflight failed" in sheet["vision"]["error"] for sheet in sheets)
