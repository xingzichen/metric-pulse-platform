from __future__ import annotations

from openpyxl import load_workbook

from metric_pulse.workbook import analyze_workbook, render_sheet_preview
from tests.conftest import EMPTY_WORKBOOK


def test_real_fixture_analysis_recognizes_all_sheets() -> None:
    analysis = analyze_workbook(EMPTY_WORKBOOK)
    assert analysis["sheet_count"] == 11
    assert len(analysis["structure_hash"]) == 64
    assert all(sheet["header_row"] == 2 for sheet in analysis["sheets"])
    assert all("logic_id" in sheet["headers"] for sheet in analysis["sheets"])
    assert any(sheet["data_rows"] > 10_000 for sheet in analysis["sheets"])
    assert any(sheet["mode"] == "snapshot_build" for sheet in analysis["sheets"])


def test_real_fixture_preview_is_png() -> None:
    workbook = load_workbook(EMPTY_WORKBOOK, read_only=True)
    sheet_name = workbook.sheetnames[0]
    workbook.close()
    preview = render_sheet_preview(EMPTY_WORKBOOK, sheet_name, max_rows=6, max_cols=8)
    assert preview.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(preview) > 1_000
