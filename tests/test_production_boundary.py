from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_TARGETS = [
    PROJECT_ROOT / "src" / "metric_pulse",
    PROJECT_ROOT / ".env.example",
    PROJECT_ROOT / "compose.yaml",
]


def test_production_runtime_has_no_expected_workbook_channel() -> None:
    forbidden = (
        "gold" + "_workbook",
        "gold" + "workbookcollector",
        "collector_mode",
        "mp_" + "gold",
    )
    violations: list[str] = []
    for target in PRODUCTION_TARGETS:
        files = target.rglob("*") if target.is_dir() else [target]
        for path in files:
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for marker in forbidden:
                if marker in text:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: {marker}")
    assert not violations, "production boundary violations:\n" + "\n".join(violations)
