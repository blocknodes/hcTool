"""泛知识（qa）域。

开放域知识问答 / 元信息查询 → fan_knowledge_agent（参数按产品契约构造：
messages + riskRespStrategy 必填，sceneType/need_media 见 fanparams）。
规则层按高置信问答信号命中即出；兜底恒 fan_knowledge_agent。
任意路径（badcase/规则/LLM fill/兜底）出的 fan_knowledge_agent 参数都必须走 fanparams。
"""

from __future__ import annotations

from pathlib import Path

from app.domain import Domain, _load_schema_json

from .rules import apply as _rule_apply
from .fallback import fallback as _fallback
from .fanparams import fan_params

_dir = Path(__file__).resolve().parent

tools = _load_schema_json(_dir)

def _post_params(tool_name: str, params: dict) -> dict:
    """fan_knowledge_agent 的最终参数必须按产品契约（messages+riskRespStrategy 必填）。
    LLM fill 路径可能只给出 messages，这里统一补全，保证任一来源参数完整。"""
    if tool_name == "fan_knowledge_agent":
        q = ""
        msgs = params.get("messages") if isinstance(params, dict) else None
        if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
            q = msgs[0].get("content", "") or ""
        elif isinstance(params, dict):
            q = params.get("content") or params.get("query") or ""
        return fan_params(q)
    return params


domain: Domain = Domain(
    key="qa",
    name="泛知识",
    tools=tools,
    rule_select=_rule_apply,
    fallback=_fallback,
    postprocess=_post_params,
)
