from __future__ import annotations

import asyncio
import os

import pytest

from metric_pulse.config import Settings
from metric_pulse.omlx import OMLXClient
from metric_pulse.workbook import analyze_workbook, render_sheet_preview
from tests.conftest import EMPTY_WORKBOOK


@pytest.mark.omlx
def test_local_omlx_understands_real_workbook_preview() -> None:
    if os.environ.get("MP_OMLX_INTEGRATION") != "1":
        pytest.skip("set MP_OMLX_INTEGRATION=1 to exercise the local multimodal model")
    settings = Settings(
        omlx_api_key=os.environ["MP_OMLX_API_KEY"],
        vision_analysis_enabled=True,
    )
    sheet = analyze_workbook(EMPTY_WORKBOOK)["sheets"][0]
    result = asyncio.run(
        OMLXClient(settings).generate_json(
            system="Inspect the supplied spreadsheet image and return one JSON object only.",
            prompt=(
                'Return exactly {"spreadsheet_visible": boolean, "has_table_headers": boolean}. '
                "Do not guess text that is not visible."
            ),
            image_png=render_sheet_preview(EMPTY_WORKBOOK, sheet["name"]),
        )
    )
    assert result["spreadsheet_visible"] is True
    assert result["has_table_headers"] is True
