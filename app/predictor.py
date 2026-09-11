"""LLM-first 预测编排：两次 function-calling。

- 第 1 次：把候选工具全量交给模型，让它只做工具选择（忽略这一次填的参数）。
- 第 2 次：只交给模型选中的那一个工具，让它专注填参。
- 全程不做确定性改写（route_override / postproc / 规则），也暂不做违规校验与自修复。
"""

from __future__ import annotations

import json
from typing import Any

from .llm import chat, first_tool_call
from .models import Prediction, PredictRequest
from .registry import Tool, load_registry

SELECT_PROMPT = """你是电视语音助手的意图解析器。根据用户的话，从提供的工具中选出最合适的一个。

原则：
- 通过 function calling 直接调用你选中的那个工具，不要输出解释文字。
- 只需选对工具；参数这一步不重要，可随意填占位值。
- 严格按每个工具的 description 判断适用场景。
"""

FILL_PROMPT = """你是电视语音助手的参数抽取器。已经为你选定了唯一的工具，请根据用户的话填好它的参数。

原则：
- 通过 function calling 调用给定的这个工具，不要输出解释文字。
- 只抽取用户明确表达的信息，不臆造、不补充用户没说的条件；用户没提的可选字段留空。
- 严格按参数 schema 里每个字段的 description / enum 写的口径取值。
- 如果有 retext 字段，原样填写用户完整原话，不改写、不截断。
"""


def _domain_key_to_name(domain_key: str) -> str:
    return {
        "vod": "影视", "audio": "有声", "children": "少儿", "education": "教育",
        "sports": "体育", "music": "音乐", "device": "设备", "fan_agent_qa": "泛知识",
    }.get(domain_key, domain_key)


def _metadata_hint(metadata: dict[str, Any] | None) -> str:
    """把外部分词/维表等信号拼成提示。仅作候选证据，不强制改写结果。"""
    if not metadata:
        return ""
    try:
        blob = json.dumps(metadata, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(metadata)
    return (
        "\n\n外部辅助信号（候选证据，不是答案，仅供参考）：\n" + blob
    )


def _build_messages(system_prompt: str, req: PredictRequest) -> list[dict[str, Any]]:
    user = f"用户的话：{req.query}" + _metadata_hint(req.metadata)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


async def predict(req: PredictRequest) -> Prediction:
    registry = load_registry()
    candidates = registry.candidates(req.domain)
    if not candidates:
        return Prediction(error="没有可用工具（schema 未加载或指定域为空）")

    # ---- 第 1 次：LLM 选工具 ----
    select_tools = [t.as_openai_function() for t in candidates]
    try:
        message = await chat(_build_messages(SELECT_PROMPT, req), tools=select_tools, tool_choice="required")
    except Exception as exc:
        return Prediction(error=f"工具选择调用失败：{exc}")

    name, _ = first_tool_call(message)
    tool = registry.by_name.get(name)
    if tool is None:
        return Prediction(error=f"模型未选出有效工具（tool={name!r}）")

    # ---- 第 2 次：LLM 填参数（只交给选中的那一个工具，强制调用它）----
    fill_tools = [tool.as_openai_function()]
    forced = {"type": "function", "function": {"name": tool.name}}
    try:
        fill_msg = await chat(_build_messages(FILL_PROMPT, req), tools=fill_tools, tool_choice=forced)
    except Exception as exc:
        return Prediction(error=f"参数填充调用失败：{exc}")

    _, params = first_tool_call(fill_msg)
    if params is None:
        return Prediction(error="模型未返回有效参数")

    return Prediction(
        domain=tool.domain or _domain_key_to_name(req.domain or ""),
        tool=tool.name,
        params=params,
        error="",
    )
