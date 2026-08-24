from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def test_alembic_upgrade_head_succeeds_on_brand_new_database(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "fresh.db"
    environment = {
        **os.environ,
        "MP_DATABASE_URL": f"sqlite:///{database_path}",
        "MP_OBJECT_ROOT": str(tmp_path / "objects"),
        "MP_EXPORT_ROOT": str(tmp_path / "exports"),
    }

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert "source_acquisition_attempts" in inspector.get_table_names()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0006_direct_source_acquisition"
            )
    finally:
        engine.dispose()
