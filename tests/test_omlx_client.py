from __future__ import annotations

import asyncio

import httpx

from metric_pulse.config import Settings
from metric_pulse.omlx import OMLXClient


def test_generate_json_uses_qwen_compatible_parameters(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "id": "response-1",
                "choices": [{"message": {"content": '{"values":{"answer":null}}'}}],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 80},
                },
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url, *, headers, json):
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        omlx_api_key="test-key",
        vision_analysis_enabled=False,
    )

    client = OMLXClient(settings)
    result = asyncio.run(
        client.generate_json(
            system="Return one JSON object.",
            prompt="Extract a value.",
        )
    )

    assert result == {"values": {"answer": None}}
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in captured["payload"]
    assert client.last_response_metadata == {
        "usage": {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
        "cached_prompt_tokens": 80,
        "cache_hit": True,
        "response_id": "response-1",
    }


def test_generate_json_is_globally_serialized(monkeypatch, tmp_path) -> None:
    active = 0
    maximum = 0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "{}"}}]}

    class FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url, *, headers, json):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(omlx_lock_path=tmp_path / "single.lock", vision_analysis_enabled=False)
    client = OMLXClient(settings)

    async def run_calls() -> None:
        await asyncio.gather(
            client.generate_json(system="json", prompt="one"),
            client.generate_json(system="json", prompt="two"),
        )

    asyncio.run(run_calls())
    assert maximum == 1
