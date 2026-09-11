"""OpenAI 兼容网关的异步最小客户端：带超时与重试，支持 native function calling。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .config import get_settings

# 网关调用的固定参数
TEMPERATURE = 0.0
REQUEST_TIMEOUT = 120.0
MAX_RETRY = 3


class LLMError(Exception):
    """网关调用失败或返回无法解析。"""


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> dict[str, Any]:
    """调用 /chat/completions，返回 choices[0].message。失败抛 LLMError。"""
    settings = get_settings()
    if not settings.api_base or not settings.api_key:
        raise LLMError("HC_API_BASE / HC_API_KEY 未配置")

    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
    }
    if not settings.model.startswith("gpt-5"):
        payload["temperature"] = TEMPERATURE
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }

    last_error = ""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for attempt in range(MAX_RETRY):
            try:
                resp = await client.post(settings.chat_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                last_error = f"HTTP {code}: {exc.response.text[:200]}"
                # 4xx 里除 429 外都是请求本身问题，重试无意义
                if code != 429 and 400 <= code < 500:
                    break
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(1.5 * (attempt + 1))

    raise LLMError(last_error or "unknown gateway error")


def first_tool_call(message: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """从 message 里取出（工具名, 参数）。兼容模型把 JSON 塞进 content 的情况。"""
    calls = message.get("tool_calls") or []
    if calls:
        function = calls[0].get("function") or {}
        name = function.get("name", "")
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = None
        return name, args if isinstance(args, dict) else None

    # 兜底：content 里可能是 ```json ... ``` 或裸 JSON
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        args = _parse_json_block(content)
        if isinstance(args, dict):
            return args.get("tool", "") or args.get("name", ""), args.get("params") or args
    return "", None


def _parse_json_block(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1] if "```" in stripped[3:] else stripped[3:]
        stripped = stripped.lstrip("json").lstrip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
            return value
        except json.JSONDecodeError:
            continue
    return None
