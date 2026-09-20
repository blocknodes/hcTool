"""影视（vod）域规则后处理：把宽松生成的 params 拧到金标准 DSL 口径。

对齐 compare 的 _normalize_vod 思路，仅做与业务无关的 DSL 结构规范化：
1. 单元素 and/or 拍平（collapse）；
2. action 由 query 起播/搜索 cue 归一并修正与 sort 冲突；
3. 叶子字段按约定顺序重排（title 置前）并去重；
4. sort 推导（新出的/最新→new desc，好看/热播→hot desc）；
5. retext 缺失时用 query 补。

仅对 vod_search / vod_search_all 生效，其余 tool 原样返回。字段名与 schema 对齐（snake_case）。

约定：normalize(tool, params) -> dict
"""

from __future__ import annotations

import re
from typing import Any

from . import dsl as _dsl  # 复用 dsl 权威 action 判定，避免 postproc 覆盖 dsl 已算对的 action

_PUNCT = re.compile(r"[，、。；：？！“”‘’（）《》\s]")


def _strip_punct(s: str) -> str:
    """去全半角标点与空白（金标准 fuzzy retext 口径：逗号/顿号/问号/引号/书名号全剥）。"""
    return _PUNCT.sub("", s)


__all__ = ["normalize", "postprocess"]


# ---------- DSL 结构工具 ----------
def _collapse(node: Any) -> Any:
    """单元素 and/or 拍平成该条件本身（与金标准约定一致）。"""
    if isinstance(node, list):
        return [_collapse(x) for x in node]
    if not isinstance(node, dict):
        return node
    if len(node) == 1:
        key, value = next(iter(node.items()))
        if key in {"and", "or"} and isinstance(value, list) and len(value) == 1:
            return _collapse(value[0])
    return {key: _collapse(value) for key, value in node.items()}


def _str_v(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _collect_leaves(node: Any, out: list[dict]) -> None:
    """深度收集所有带 field 的叶子节点（引用）。"""
    if isinstance(node, dict):
        if node.get("field"):
            out.append(node)
        for v in node.values():
            _collect_leaves(v, out)
    elif isinstance(node, list):
        for x in node:
            _collect_leaves(x, out)


# 字段优先级：title 最前，其余按常用维度顺序，越靠后越次要
_FIELD_PRIORITY = {
    "title": 0, "series": 1, "video_index": 2, "voiceStartPos": 3,
    "director": 4, "actor": 5, "area": 6, "language": 7, "channel": 8,
    "category": 9, "tag": 10, "company": 11, "vender_name": 12,
    "is_fee": 13, "is_over": 14, "release_time": 15, "rate": 16,
    "age_range": 17, "definition": 18, "sound": 19, "writer": 20,
    "prize": 21, "role": 22, "dubbing": 23, "hostess": 24, "comedy_brand": 25,
    "creation_source": 26,
}


def rebuild_query(query_node: Any) -> Any:
    """叶子去重 + 按优先级重排成统一 and 结构。非叶子（or/not 嵌套）保留原位不深排。"""
    if not isinstance(query_node, dict):
        return query_node
    # 只对顶层 and 结构做扁平重排；or/not 复合保留原样。
    if "and" in query_node:
        leaves: list[dict] = []
        seen: set[tuple] = set()
        for c in query_node["and"]:
            if not (isinstance(c, dict) and c.get("field")):
                leaves.append(c)  # 非叶子（or/not 嵌套）保留
                continue
            if "values" in c:
                key = (c.get("field"), "values", _str_v(c.get("values")), _str_v(c.get("operator")))
            else:
                key = (c.get("field"), _str_v(c.get("value")))
            if key in seen:
                continue
            seen.add(key)
            leaves.append(c)
        leaves.sort(key=lambda c: _FIELD_PRIORITY.get(c.get("field"), 99))
        return {"and": leaves} if len(leaves) > 1 else (leaves[0] if leaves else query_node)
    return query_node


# ---------- action / sort ----------
_PLAY_CUES = ("播放", "请播", "播一下", "播个", "放", "我要看", "我想看", "打开", "收看")


def _determine_action(query: str) -> str:
    if query.startswith("看") and not re.search(r"喜欢|爱看|好看|收藏", query):
        return "play"
    if any(cue in query for cue in _PLAY_CUES):
        return "play"
    return "search"


def _is_play_sort(query: str) -> bool:
    return bool(re.search(r"看到最后|看到最新|看到第", query))


def _apply_sort(result: dict, query: str) -> None:
    # 命中才找，避免产生空的 sort:{}；用 setdefault 合并而非覆盖，保留 dsl 已算的维度
    new = re.search(r"新出|最新|最近|新上|新播|新剧|近期|刚上|刚更新|这几天|新片|新看", query)
    hot = re.search(r"好看|热播|热门|大家都在看|热度的|热度高|热门的|爆款|爆火|热$|很火|火热的?|最热|热度", query)
    rate = re.search(r"评分高|高分|高评分|评分.{0,2}高", query)
    play = re.search(r"播放(?:量|数)(?:最高|多|大)?|播放最高|播放次数多|高播放量", query)
    if not (new or hot or rate or play):
        return
    so = result.setdefault("sort", {})
    if new: so.setdefault("new", {"order": "desc"})
    if hot: so.setdefault("hot", {"order": "desc"})
    if rate: so.setdefault("rate", {"order": "desc"})
    if play: so.setdefault("play", {"order": "desc"})


def normalize(tool: str, params: dict) -> dict:
    """规范化 vod_search / vod_search_all / vod_fuzzy_search 的 DSL 参数结构。"""
    if not isinstance(params, dict):
        return params
    # fuzzy：整句语义检索，param=去标点的 retext（金标准口径；含口语归一：追剧→电视剧、
    # 影片→电影、半角数字→中文，由 dsl._fuzzy_retext_norm 提供）
    if tool == "vod_fuzzy_search":
        # 模型偶尔把条件树写进 query（fuzzy 只吃整句），此时必须让位给 retext，
        # 否则下面 _strip_punct 会收到 dict 抛 TypeError。
        q = params.get("retext") or params.get("query") or ""
        if not isinstance(q, str):
            q = ""
        q = _strip_punct(q)
        q = _dsl._fuzzy_retext_norm(q)
        return {"retext": q}
    if tool not in {"vod_search", "vod_search_all"}:
        return params

    result = dict(params)
    q = str(result.get("retext") or "")

    # 1) 单元素 and/or 拍平
    if isinstance(result.get("query"), (dict, list)):
        result["query"] = _collapse(result["query"])

    # 2) action：dsl 已权威算出（点播/浏览都判对了），这里只补缺失，不覆盖。
    #    否则 postproc 的粗略 _determine_action 会把 dsl 的 search 误改 play
    #    （如「我想看韩剧/我要看免费的影片」期望 search）。
    if not result.get("action"):
        if _is_play_sort(q):
            result["action"] = "search"
        else:
            result["action"] = _determine_action(q)

    # 3) query 顶层 and 结构重排 + 去重（保留 operator：多值 values 需 or/and 语义）
    qn = result.get("query")
    if isinstance(qn, dict) and ("field" in qn or "and" in qn or "or" in qn):
        result["query"] = rebuild_query(qn)

    # 4) sort 推导
    _apply_sort(result, q)

    # 5) retext 兜底
    result.setdefault("retext", q)

    # 6) 单元素 and（and: [单叶子]）→ 拍平（金标准：最近播放量高的新剧 → 纯叶子 query；
    #    其余 and 组合保留）。仅当 and 列表恰含 1 个叶子且无其他嵌套。
    _qn = result.get("query")
    if isinstance(_qn, dict) and _qn.get("and") and len(_qn["and"]) == 1 \
            and isinstance(_qn["and"][0], dict) and _qn["and"][0].get("field"):
        result["query"] = _qn["and"][0]
    return result


# 兼容 expose
postprocess = normalize