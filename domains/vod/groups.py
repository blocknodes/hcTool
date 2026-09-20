"""已废弃：vod 旧的「5 组工具选择」架构（LLM 组选 → 组内规则 → 组内兜底）。

2026-09-19 起 vod 改为 **fewshot 缓存直出 + 单次 LLM 出 tool&params**，
决策主轴迁到 `pipeline.py`（`pipeline()`，经 `Domain.pipeline` 钩子由 engine 接管）。
本模块保留为兼容薄壳：旧引用（如 `select_group_by_rules`）仍可导入，
但内部转调新流水线，不再维护组定义 / 组内规则 / 组选提示词。

历史设计的教训（保留备查，勿再照搬）：
- 组内兜底直接甩 `vod_fuzzy_search`，丢掉了 L3 `fallback.fallback()` 的
  `_FUZZY_TOTAL` 语义前置 + `dsl.route_tool` 三级覆盖能力，确定性路径 tool 只有
  257/266（比原规则栈差）。
- `fallback_route` 钩子在 `select_in_group` 里读了，但 `GROUP_DEFS["search"]`
  从未设置该 key —— 钩子悬空。
"""

from __future__ import annotations

import logging

from .pipeline import pipeline  # noqa: F401 新决策主轴

logger = logging.getLogger("hcTools.domains.vod.groups")


def tool_to_group(tool_name: str) -> str:
    """兼容旧接口：工具 → 组名（仅用于日志/审计，不再参与决策）。"""
    if tool_name in {"vod_search", "vod_search_all", "vod_fuzzy_search"}:
        return "search"
    return {
        "vod_history": "history",
        "vod_relate_search": "relate",
        "vod_person_search": "person",
        "vod_personalized_search": "personalized",
    }.get(tool_name, "search")


def select_group_by_rules(query: str) -> str:
    """兼容旧接口：已废弃，恒返回 "search"。"""
    logger.warning("select_group_by_rules 已废弃（vod 改为单次 LLM 流水线），query=%r", query)
    return "search"
