"""少儿(children)域后处理：query 结构规范 + retext 兜底。"""
from __future__ import annotations


def _collapse(node):
    if isinstance(node, list):
        return [_collapse(x) for x in node]
    if not isinstance(node, dict):
        return node
    if len(node) == 1:
        k, v = next(iter(node.items()))
        if k in ("and",) and isinstance(v, list) and len(v) == 1:
            return _collapse(v[0])
    return {k: _collapse(v) for k, v in node.items()}


def normalize(tool: str, params: dict) -> dict:
    if not isinstance(params, dict):
        return params or {}
    params = dict(params)
    qn = params.get("query")
    if isinstance(qn, (dict, list)):
        params["query"] = _collapse(qn)
    # retext：仅 search/search_all 提供；relate/history 不带 retext（golden 实证）
    if tool in ("educ_search", "educ_search_all"):
        params.setdefault("retext", params.get("retext") or "")
    elif tool == "educ_relate_recommend":
        params.pop("retext", None)
    return params


postprocess = normalize