"""LLM-first 预测编排：单次 function-calling 完成 域路由 + 工具选择 + 参数抽取。

对比旧的三段式方案，这里：
- 一次 LLM 调用把候选工具全量交给模型，让它自己选并填参；
- 不做 route_override / apply_rules / postproc 这类确定性改写；
- 只保留 schema 校验 + 一次带反馈的自修复。
"""

from __future__ import annotations

import json
from typing import Any

from .llm import chat, first_tool_call
from .models import Prediction, PredictRequest
from .registry import Tool, load_registry
from .validate import check

SYSTEM_PROMPT = """你是电视语音助手的意图解析器。根据用户的话，从提供的工具中选出最合适的一个并填好参数。

原则：
- 通过 function calling 直接调用你选中的那个工具，不要输出解释文字。
- 只抽取用户明确表达的信息，不臆造、不补充用户没说的条件；用户没提的可选字段不要填。
- 严格按每个工具 description 和参数 schema 里写的口径取值。
- 如果提供了 retext 字段，原样填写用户完整原话，不改写、不截断。
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


def _build_messages(req: PredictRequest) -> list[dict[str, Any]]:
    user = f"用户的话：{req.query}" + _metadata_hint(req.metadata)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


async def predict(req: PredictRequest) -> Prediction:
    registry = load_registry()
    tools = registry.openai_tools(req.domain)
    if not tools:
        return Prediction(error="没有可用工具（schema 未加载或指定域为空）")

    messages = _build_messages(req)
    try:
        message = await chat(messages, tools=tools, tool_choice="required")
    except Exception as exc:  # LLMError 等
        return Prediction(error=f"模型调用失败：{exc}")

    name, params = first_tool_call(message)
    tool = registry.by_name.get(name)
    if tool is None or params is None:
        return Prediction(error=f"模型未返回有效工具调用（tool={name!r}）")

    issues = check(params, tool.parameters)
    retried = False

    # 一次带反馈的自修复：只在有违规时触发
    if issues:
        followup = messages + [
            message,
            {
                "role": "user",
                "content": "上一次调用的参数不符合工具定义：\n- "
                + "\n- ".join(issues)
                + "\n请只修正违规部分后重新调用同一个工具，其余保持不变。",
            },
        ]
        try:
            fixed_msg = await chat(followup, tools=tools, tool_choice="required")
            fixed_name, fixed_params = first_tool_call(fixed_msg)
            if fixed_params is not None:
                fixed_tool = registry.by_name.get(fixed_name) or tool
                remaining = check(fixed_params, fixed_tool.parameters)
                # 只有变好才接受
                if len(remaining) <= len(issues):
                    name, tool, params, issues = fixed_name, fixed_tool, fixed_params, remaining
                retried = True
        except Exception:
            pass  # 修复失败保留原结果

    return Prediction(
        domain=tool.domain or _domain_key_to_name(req.domain or ""),
        tool=name,
        params=params,
        retried=retried,
        violations=issues,
        error="",
    )
