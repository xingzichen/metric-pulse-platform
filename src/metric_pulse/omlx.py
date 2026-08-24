"""本地 OMLX/OpenAI 兼容接口客户端。

当前只有一个 Qwen3.8-27B-6bit 模型且服务端并发固定为 1。本模块使用进程内异步锁和跨进程
文件锁串行化请求，防止多个 worker 同时占用模型。模型输出在提供方边界解析为 JSON 对象，
业务层不接收自由文本或批量行结果。
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, get_settings

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_PROCESS_MODEL_LOCK = asyncio.Lock()


class OMLXError(RuntimeError):
    """把 HTTP、协议和 JSON 解析错误统一包装为本地模型错误。"""

    pass


@asynccontextmanager
async def single_model_channel(lock_path: Path):
    """在协程和本地进程之间串行化 OMLX 请求。

    异步锁避免同一事件循环竞争；非阻塞 ``flock`` 覆盖多个 API/Celery 进程。轮询时使用
    ``asyncio.sleep``，不会阻塞健康检查和任务控制接口。
    """
    async with _PROCESS_MODEL_LOCK:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.1)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


class OMLXClient:
    """对 OMLX Chat Completions 的小型严格客户端。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.last_response_metadata: dict[str, Any] = {}

    @property
    def endpoint(self) -> str:
        return f"{self.settings.omlx_base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.omlx_api_key:
            headers["Authorization"] = f"Bearer {self.settings.omlx_api_key}"
        return headers

    async def health(self) -> dict[str, Any]:
        """检查模型列表，并确认配置要求的唯一模型确实可用。"""

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.settings.omlx_base_url.rstrip('/')}/models", headers=self._headers()
            )
            response.raise_for_status()
            models = response.json()
            entries = models.get("data", []) if isinstance(models, dict) else []
            ids = {item.get("id") for item in entries if isinstance(item, dict)}
            if ids and self.settings.omlx_model not in ids:
                raise OMLXError(
                    f"Required model {self.settings.omlx_model} is unavailable; found {sorted(ids)}"
                )
            return {"ok": True, "models": models, "model": self.settings.omlx_model}

    async def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        image_png: bytes | None = None,
    ) -> dict[str, Any]:
        """发送一次非流式请求并把响应规范化为单个 JSON 对象。

        温度固定为 0 且关闭 thinking，减少不可控延迟。视觉请求必须把图片放在文字前面以
        匹配当前 Qwen 模板。token 用量和前缀缓存命中写入 ``last_response_metadata``。
        """

        user_content: str | list[dict[str, Any]]
        if image_png:
            encoded = base64.b64encode(image_png).decode()
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                # 当前 Qwen OMLX 视觉模板要求图片 token 位于指令之前，反向排列可能返回
                # JSON token-id 数组而不是业务对象。
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
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        # 当前部署使用 response_format=json_object 时偶尔会把输入值回显成数组，所以文本和
        # 视觉请求统一使用提示词约束 JSON，再在提供方边界严格校验顶层对象。
        try:
            async with (
                single_model_channel(self.settings.omlx_lock_path),
                httpx.AsyncClient(timeout=self.settings.omlx_timeout_seconds) as client,
            ):
                response = await client.post(self.endpoint, headers=self._headers(), json=payload)
                response.raise_for_status()
            response_payload = response.json()
            usage = response_payload.get("usage", {})
            prompt_details = usage.get("prompt_tokens_details", {}) if isinstance(usage, dict) else {}
            cached_tokens = prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None
            self.last_response_metadata = {
                "usage": usage if isinstance(usage, dict) else {},
                "cached_prompt_tokens": cached_tokens,
                "cache_hit": isinstance(cached_tokens, int) and cached_tokens > 0,
                "response_id": response_payload.get("id"),
            }
            content = response_payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise OMLXError("OMLX returned non-text structured content")
            parsed = json.loads(JSON_FENCE.sub("", content).strip())
            # 兼容本地多模态模板的两种无害包装：单元素数组和“字符串中的 JSON”。最多展开
            # 两层，避免递归解析任意模型输出；展开后仍必须是对象。
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
        """结合确定性结构摘要和预览图辅助识别工作表字段角色。"""

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
