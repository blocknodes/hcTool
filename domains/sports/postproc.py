"""sports 域参数后处理：规范化 DSL 结构。

rules 已确定性生成正确结构；这里仅做与业务无关的 DSL 结构规范化：
单元素 and 拍平、叶子去重、and 序稳定。与 bench 的 canonical 对齐但非必需。
"""
from __future__ import annotations

from typing import Any


def _collapse(node: Any) -> Any:
    if isinstance(node, list):
        return [_collapse(x) for x in node]
    if not isinstance(node, dict):
        return node
    if len(node) == 1:
        key, value = next(iter(node.items()))
        if key in {"and", "or"} and isinstance(value, list) and len(value) == 1:
            return _collapse(value[0])
    return {key: _collapse(value) for key, value in node.items()}


def normalize(tool: str, params: dict) -> dict:
    if not isinstance(params, dict):
        return params or {}
    result = dict(params)
    if isinstance(result.get("query"), dict):
        result["query"] = _collapse(result["query"])
    return result


postprocess = normalize