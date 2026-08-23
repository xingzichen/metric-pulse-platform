from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from .config import Settings, get_settings

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class OMLXError(RuntimeError):
    pass


class OMLXClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def endpoint(self) -> str:
        return f"{self.settings.omlx_base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.omlx_api_key:
            headers["Authorization"] = f"Bearer {self.settings.omlx_api_key}"
        return headers

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.settings.omlx_base_url.rstrip('/')}/models", headers=self._headers()
            )
            response.raise_for_status()
            return {"ok": True, "models": response.json()}

    async def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        image_png: bytes | None = None,
    ) -> dict[str, Any]:
        user_content: str | list[dict[str, Any]]
        if image_png:
            encoded = base64.b64encode(image_png).decode()
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                # Qwen's OMLX vision template expects the image token before
                # the instruction; reversing this can yield a JSON token-id list.
                {"type": "text", "text": prompt},
            ]
        else:
            user_content = prompt
        payload = {
            "model": self.settings.omlx_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "max_tokens": self.settings.omlx_max_output_tokens,
        }
        # The current Qwen3.8 vision template in OMLX returns token-id arrays
        # when response_format is combined with image input. Text-only calls
        # still benefit from native JSON mode.
        if not image_png:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=self.settings.omlx_timeout_seconds) as client:
                response = await client.post(self.endpoint, headers=self._headers(), json=payload)
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise OMLXError("OMLX returned non-text structured content")
            parsed = json.loads(JSON_FENCE.sub("", content).strip())
            # Some local multimodal templates wrap a requested object in a
            # single-element JSON array. Normalize that harmless variation at
            # the provider boundary while keeping the application contract strict.
            for _ in range(2):
                if isinstance(parsed, list) and len(parsed) == 1:
                    parsed = parsed[0]
                    continue
                if isinstance(parsed, str) and parsed.lstrip().startswith(("{", "[")):
                    parsed = json.loads(parsed)
                    continue
                break
            if not isinstance(parsed, dict):
                length = len(parsed) if isinstance(parsed, list | str) else None
                raise OMLXError(
                    f"OMLX JSON response must be an object (type={type(parsed).__name__}, length={length})"
                )
            return parsed
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise OMLXError(str(exc)) from exc

    async def analyze_sheet(
        self,
        *,
        structure: dict[str, Any],
        preview: bytes,
    ) -> dict[str, Any]:
        prompt = (
            "Analyze this Excel sheet preview and the deterministic OOXML summary. "
            "Return JSON with descriptor_fields, target_fields, business_key_fields, mode, "
            "confidence, conflicts, and reason. Use only field names present in headers.\n"
            + json.dumps(structure, ensure_ascii=False, default=str)
        )
        return await self.generate_json(
            system=(
                "You are a workbook structure recognizer. The supplied OOXML coordinates and values "
                "are authoritative. Never invent a field or coordinate. Return JSON only."
            ),
            prompt=prompt,
            image_png=preview,
        )
