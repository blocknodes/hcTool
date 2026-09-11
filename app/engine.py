"""两段式意图解析内核：select → fill，两次独立的 LLM 调用。

- select：把该域候选工具的简介放进 prompt，让模型只输出选中的工具名。
- fill：把选中工具的参数 schema 放进 prompt，让模型只输出参数 JSON。
- 两端都注入域的 select_prompt / fill_prompt 与 few-shot；缺省用域默认。
- 全程走文本输出（网关 vLLM pipeline 未开 tool-call-parser，原生 function-calling 不通），
  不做原生 function-calling。
- 内核不含任何单域特判；前后处理由域流水线（router）负责。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .domain import Domain
from .llm import chat, first_tool_call
from .models import Prediction, PredictRequest

logger = logging.getLogger("hcTools.engine")


def _metadata_hint(metadata: dict[str, Any] | None) -> str:
    """把外部分词/维表等信号拼成提示。仅作候选证据，不强制改写结果。"""
    if not metadata:
        return ""
    try:
        blob = json.dumps(metadata, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(metadata)
    return "\n\n外部辅助信号（候选证据，不是答案，仅供参考）：\n" + blob


def _build_messages(system_prompt: str, req: PredictRequest, fewshot: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": system_prompt}]
    for example in fewshot or []:
        _append_example(messages, example)
    messages.append({"role": "user", "content": f"用户的话：{req.query}" + _metadata_hint(req.metadata)})
    return messages


def _append_example(messages: list[dict[str, Any]], example: dict[str, Any]) -> None:
    """把一条 few-shot 示例展开成 assistant/user 两轮（tool_calls + result 可选）。"""
    user = example.get("user")
    if not user:
        return
    messages.append({"role": "user", "content": f"用户的话：{user}"})
    tool_call = example.get("tool_call")
    if tool_call:
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "type": "function",
                "function": {
                    "name": tool_call.get("name", ""),
                    "arguments": json.dumps(tool_call.get("arguments", {}), ensure_ascii=False),
                },
            }],
        })
    tool_result = example.get("tool_result")
    if tool_result is not None:
        messages.append({
            "role": "tool",
            "tool_call_id": "example",
            "content": tool_result if isinstance(tool_result, str) else json.dumps(tool_result, ensure_ascii=False),
        })


def _full_examples(domain: Domain, stage: str) -> list[dict[str, Any]]:
    """返回该域在当前阶段可用的 few-shot；先仅支持 select/fill 通用样例。"""
    out: list[dict[str, Any]] = []
    for ex in domain.fewshot or []:
        scoped = ex.get("stages")
        if scoped and stage not in scoped:
            continue
        out.append(ex)
    return out


def _match_tool_name(text: str, candidates: dict[str, Any]) -> str | None:
    """从模型 content 文本里尽可能匹配出工具名（compare 的成熟兜底）。"""
    answer = text.strip().strip("`\"'\n ")
    if answer in candidates:
        return answer
    hits = [name for name in candidates if name in answer]
    if len(hits) == 1:
        return hits[0]
    return None


async def select_tool(req: PredictRequest, domain: Domain) -> tuple[str | None, str]:
    """返回 (tool_name, error)。error 非空表示选择失败。

    主路径：让模型输出纯文本工具名（兼容不支持原生 function-calling 的网关，
    同 compare 的 select 做法）。输出里若出现合法 JSON 也尝试从 node 抽取。
    """
    candidates = domain.tools
    if not candidates:
        return None, "该域没有可用工具"
    if len(candidates) == 1:
        return candidates[0].name, ""

    briefs = "\n".join(
        f"- {t.name}：{' '.join(t.description.split())[:200]}" for t in candidates
    )
    select_messages = [
        {"role": "system", "content": domain.select_prompt},
        {
            "role": "user",
            "content": (
                f"候选工具：\n{briefs}\n\n用户的话：{req.query}"
                + _metadata_hint(req.metadata)
                + "\n\n输出最合适的工具名："
            ),
        },
    ]
    message = await chat(select_messages)

    # 原生 tool_calls 优先；否则从 content 文本/JSON 里匹配工具名
    name, parsed = first_tool_call(message)
    if name in domain.tools_by_name:
        return name, ""
    content = message.get("content")
    if isinstance(parsed, dict):
        hit = parsed.get("tool") or parsed.get("name") or parsed.get("tool_name")
        if hit in domain.tools_by_name:
            return hit, ""
    if isinstance(content, str):
        hit = _match_tool_name(content, domain.tools_by_name)
        if hit:
            return hit, ""
    return None, f"模型未选出有效工具（原始输出：{(content or name)!r}）"


async def fill_params(req: PredictRequest, domain: Domain, tool_name: str) -> tuple[dict[str, Any] | None, str | None]:
    """返回 (params, error)。

    直接走文本 JSON：把选中工具的 schema 注进 prompt，让模型只输出参数 JSON。
    （网关是 vLLM pipeline，未开 tool-call-parser，原生 function-calling 一律 400，
    故不尝试；同 compare 的 json 模式。）
    """
    tool = domain.tools_by_name.get(tool_name)
    if tool is None:
        return None, f"未知工具：{tool_name}"

    schema = json.dumps(tool.parameters, ensure_ascii=False)
    text_messages = [
        {"role": "system", "content": domain.fill_prompt + f"\n\n参数的 JSON Schema 如下：\n{schema}\n只输出参数 JSON 对象本身。"},
        {"role": "user", "content": f"用户的话：{req.query}" + _metadata_hint(req.metadata)},
    ]
    try:
        fill_msg = await chat(text_messages)
    except Exception as exc:  # noqa: BLE001
        return None, f"参数填充调用失败：{exc}"

    from .llm import _parse_json_block
    params = _parse_json_block(str(fill_msg.get("content") or ""))
    if params is None:
        return None, "模型未返回有效参数"
    return params, None


async def run(req: PredictRequest, domain: Domain) -> Prediction:
    """单域两段式流水线（不含前后处理，由 router 包裹）。"""
    tool_name, err = await select_tool(req, domain)
    if err:
        return Prediction(domain=domain.name, error=err)

    raw_params, err = await fill_params(req, domain, tool_name)
    if err:
        return Prediction(domain=domain.name, error=err)
    if raw_params is None:
        return Prediction(domain=domain.name, error="模型未返回有效参数")

    params = raw_params
    if domain.postprocess:
        try:
            params = domain.postprocess(tool_name, params)
            if not isinstance(params, dict):
                params = raw_params
        except Exception as exc:  # noqa: BLE001 后处理失败不影响主流程
            logger.error("域 %s 后处理失败：%s", domain.key, exc)

    return Prediction(domain=domain.name, tool=tool_name, params=params, error="")