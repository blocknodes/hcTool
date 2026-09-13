"""规则库内核：把原来散在 `rules.apply()` 的 if-else 顺序判定提成显式规则条目。

与 detect 抽离同一组诉求：
- 可审计：每条规则有 id / priority / title / explain / enabled，能分清"哪条规则、为什么、改了什么"。
- 可拔插：enabled 关掉即停；可枚举命中、可出审计。
- 可解释：apply 命中时可附 rule_id，日志/审计一眼可见。

通用数据模型（覆盖各域形态）：
- `decide(query)`：等价原 apply 里**一个分支**的完整判定。返回 `(tool, params)`（params 可为 None，
  表示"该 tool 命中但参数交给 LLM fill"，与 vod/device 语义一致）或 `None`（未命中，继续下一条）。
- `match_re`（可选）：声明式命中正则；当只有它而无 decide 时，命中即返回 `(tool, {})`。
- 顺序 = priority 升序；default 是无 matcher 的最终兜底。

关键语义保证与改造前完全一致：
- params 为 None 表示"只定工具、参数走 LLM"，不是错误，会在上层 rule_llm_fill 兜底。
- decide 返回 None 视为"这条未命中"，继续下一条，最终可落到 default / LLM / fallback。

用法（域内 rules.py）：
    RULES = RuleSet([
        Rule(id="vod_history", tool="vod_history", priority=1,
             decide=_history_branch,        # _history_branch(q) -> ("vod_history", params) | None
             title="历史", explain="…"),
        Rule(id="vod_fuzzy_fragment", tool="vod_fuzzy_search", priority=4,
             match_re=r"片段|台词|名场面",   # 简单正则：命中即 (tool, {"query": q})
             build=lambda q: {"query": q}),
    ])
    def apply(q): return RULES.select(q)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("hcTools.rulebase")

# (query) -> (tool, params|None) | None  |  params=None 表示"工具定了，参数交 LLM"
RuleDecider = Callable[[str], tuple[str, Any | None] | None]
# (query) -> dict | None                    用于基于 match_re 的简单参数组装
RuleBuilder = Callable[[str], dict[str, Any] | None]


@dataclass
class Rule:
    id: str
    tool: str
    priority: int = 100
    title: str = ""
    explain: str = ""
    scope: str = "both"              # bright | off | both
    enabled: bool = True
    match_re: str | None = None      # 命中即触发（配合 build）；有 decide 时此项可省
    build: RuleBuilder | None = None # 命中后组的 params
    decide: RuleDecider | None = None  # 完整分支判定（优先于 match_re/build）
    note: str = ""

    _re: re.Pattern | None = field(default=None, init=False, repr=False)

    def _regex(self) -> re.Pattern | None:
        if self._re is None and self.match_re:
            self._re = re.compile(self.match_re)
        return self._re

    def matched(self, query: str) -> str | None:
        """返回匹配到的子串（审计）；无 match_re 返回 None。"""
        rx = self._regex()
        if rx is None:
            return None
        m = rx.search(query)
        return m.group(0) if m else None

    def hits(self, query: str) -> bool:
        """该规则对 query 是否命中（不 constructing params，只判信号）。audit 用。"""
        if self.decide is not None:
            # decide 规则：无法用正则轻判，只能真跑（成本高，audit 不频繁用）
            return None  # 需要真跑 decide
        return self.matched(query) is not None


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)
    default: Rule | None = None       # 最终兜底（无 matcher，恒跑，返回 None 表示整链放弃）

    def __post_init__(self) -> None:
        self.rules.sort(key=lambda r: r.priority)
        seen: dict[int, str] = {}
        for r in self.rules:
            if r.priority in seen:
                logger.warning("priority=%s 冲突：%s 与 %s", r.priority, seen[r.priority], r.id)
            seen[r.priority] = r.id

    def _run(self, r: Rule, query: str):
        """对单条规则跑判定，返回 (tool, params) 或 None(=未命中)。"""
        # decide 优先：完整分支判定，返回 (tool, params)|None
        if r.decide is not None:
            try:
                return r.decide(query)
            except Exception as exc:  # noqa: BLE001 单规则失败不拖垮
                logger.error("规则 %s decide 异常：%s", r.id, exc)
                return None
        # 无 decide：match_re 命中才继续
        if not r.match_re:
            return None
        if r.matched(query) is None:
            return None
        if r.build is not None:
            try:
                p = r.build(query)
            except Exception as exc:  # noqa: BLE001
                logger.error("规则 %s build 异常：%s", r.id, exc)
                return None
            return (r.tool, p)
        return (r.tool, {})

    def select(self, query: str):
        """按 priority 升序找首条命中；返回 (tool, params) 或 None。params 可能是 None（工具定·参数交 LLM）。"""
        for r in self.rules:
            if not r.enabled:
                continue
            hit = self._run(r, query)
            if hit is not None:
                return hit
        if self.default is not None and self.default.enabled:
            try:
                d = self.default.decide(query) if self.default.decide is not None else self._run(self.default, query)
                if d is not None:
                    return d
            except Exception as exc:  # noqa: BLE001
                logger.error("default 规则异常：%s", exc)
        return None

    def select_with_rule(self, query: str):
        """同 select，但返回 (tool, params, rule)。审计用。"""
        for r in self.rules:
            if not r.enabled:
                continue
            hit = self._run(r, query)
            if hit is not None:
                return hit[0], hit[1], r
        if self.default is not None and self.default.enabled:
            try:
                d = self.default.decide(query) if self.default.decide is not None else self._run(self.default, query)
                if d is not None:
                    return d[0], d[1], self.default
            except Exception as exc:  # noqa: BLE001
                logger.error("default decide 异常：%s", exc)
        return None


__all__ = ["Rule", "RuleSet"]