"""sports 域参数后处理：规范化 DSL 结构。

- `and` 单元素拍平：`{"and":[X]}` → `X`（golden 从不为单条件包 and）。
- **裸数组补 and**：LLM 常把多条件写成 `{"query":[{...},{...}]}`，而 golden 的
  `QueryNode` 恒为对象、多条件一律包 `{"and":[...]}`。实测纯 LLM 通道 134 条 miss 中
  75 条是这个单一形状问题 —— 修它一条，等价于修 56% 的 miss。
- 叶子去重（同 field+value 重复出现的，如模型把「搏击格斗」拆成两个 sport_name）。
"""
from __future__ import annotations

from typing import Any


def _collapse(node: Any) -> Any:
    if isinstance(node, list):
        # 裸数组 = 多条件并列，补成 and；单元素则直接拍平
        node = [_collapse(x) for x in node]
        node = _dedup(node)
        if len(node) == 1:
            return node[0]
        return {"and": node} if node else {}
    if not isinstance(node, dict):
        return node
    if len(node) == 1:
        key, value = next(iter(node.items()))
        if key in {"and", "or"} and isinstance(value, list):
            value = _dedup([_collapse(x) for x in value])
            if len(value) == 1:
                return value[0]
            return {key: value}
    return {key: _collapse(value) for key, value in node.items()}


def _dedup(leaves: list) -> list:
    """按规范化签名去重，保持首次出现顺序。"""
    import json

    out, seen = [], set()
    for x in leaves:
        sig = json.dumps(x, ensure_ascii=False, sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(x)
    return out


def normalize(tool: str, params: dict) -> dict:
    if not isinstance(params, dict):
        return params or {}
    result = dict(params)
    if "query" in result and isinstance(result["query"], (dict, list)):
        result["query"] = _collapse(result["query"])
    return result


postprocess = normalize