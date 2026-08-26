from __future__ import annotations

import tempfile

import pytest

from metric_pulse.config import Settings


def test_attachment_and_request_profile_defaults_are_bounded() -> None:
    settings = Settings()

    assert settings.source_user_agent.startswith("Mozilla/5.0")
    assert settings.source_accept_language.startswith("zh-CN")
    assert settings.attachment_discovery_enabled is True
    assert settings.attachment_max_per_parent == 5
    assert settings.attachment_max_per_unit == 8
    assert settings.attachment_max_total_bytes == 50_000_000


def test_storage_startup_probe_rejects_unwritable_source_cache(monkeypatch, tmp_path) -> None:
    source_cache = tmp_path / "source-cache"
    original = tempfile.NamedTemporaryFile

    def fail_source_cache_probe(*args, **kwargs):
        if kwargs.get("dir") == source_cache:
            raise PermissionError(13, "Permission denied", str(source_cache))
        return original(*args, **kwargs)

    monkeypatch.setattr("metric_pulse.config.tempfile.NamedTemporaryFile", fail_source_cache_probe)
    settings = Settings(
        object_root=tmp_path / "objects",
        export_root=tmp_path / "exports",
        source_cache_root=source_cache,
        omlx_lock_path=tmp_path / "omlx.lock",
        vision_analysis_enabled=False,
    )

    with pytest.raises(RuntimeError, match="source cache directory is not writable"):
        settings.ensure_directories()
