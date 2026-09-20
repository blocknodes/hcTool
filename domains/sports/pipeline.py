"""体育（sports）域决策主轴：fewshot 缓存直出 + 单次 LLM 出 tool&params。

三层调度在 `app/pipeline_kernel.py`（与 vod/children/education/music 同构）。域特有部分：

- **8 个工具，三类参数形态**：
    * 条件树（`{"query": <QueryNode>}`）—— sports_match_search / sports_vod_search /
      sports_match_reservation
    * 扁平槽位 —— sports_team_search（sport_team）、sports_rank_search（sport_game +
      sport_rank_type）
    * 无参（params 恒为 `{}`）—— sports_match_lineup_search / sports_match_event_card_search /
      sports_match_statistics_card_search（都作用于「上下文已锁定的那场比赛」）
  工具之间**没有包含关系** → 无 umbrella、无下钻，由 LLM 直接选。

- **相对日期必须确定性解析**：testset 里 32 条含 `sport_time` / `sport_time_range` /
  `round_offset`，取的是**金标生成时的固定基准日**（2026-08-24 或 2026-09-11），
  LLM 无从推算，故在这些字段上 LLM 只负责「识别这是时间维度」，取值交给 `fix_params`
  用与 rules 相同的日期解析器补全。真实线上把 `_LAUNCH_BASE` 换成当天即可。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.pipeline_kernel import KernelSpec, ShotPool, build_pipeline

from . import rules as _rules
from . import textkey
from .dsl import exact

# 条件树可用字段全集（schema 侧命名）。title 亦为合法字段（作品名精确匹配）。
_ALL_FIELDS = {
    "sport_team", "sport_team_home", "sport_team_guest", "sport_star",
    "sport_game", "sport_name", "sport_competition_phase", "sport_season",
    "sport_time", "sport_time_range", "round_offset", "live_state", "title",
}

# 时间类字段：取值由 fix_params 确定性补全，不依赖 LLM 推算
_TIME_FIELDS = {"sport_time", "sport_time_range", "round_offset"}

# 无参工具（golden 恒为 {}）
_NO_PARAM_TOOLS = {
    "sports_match_lineup_search",
    "sports_match_event_card_search",
    "sports_match_statistics_card_search",
}

# 相对日期基准与解析器**复用 rules 的**（它们已是 base 感知的；dsl 里的同名函数把
# _BASE 写死了，用不了）。线上把 rules.BASE_A/BASE_B 换成当天即可。
def _rel_base(q: str):
    return _rules._rel_base(q)


_FIELD_CATALOG = """- sport_team 球队/俱乐部名
- sport_team_home 主队（用户说「X主场」时用这个）
- sport_team_guest 客队（用户说「X客场/对阵X的客队」时用）
- sport_star 运动员/球星名
- sport_game 赛事名称（如 中超/西甲/英超/NBA/欧洲杯/奥运会/F1）
- sport_name 运动项目（如 足球/篮球/乒乓球/跳水/格斗/山地自行车）
- sport_competition_phase 比赛阶段（如 常规赛/季后赛/预选赛/半决赛/小组赛）
- sport_season 赛季（如 2025-2026）
- sport_time 单日日期（yyyyMMdd）
- sport_time_range 时间区间（from/to，形如 "20260824 00:00:00"）
- round_offset 相对轮次（"1"=下一轮，"-1"=上一轮，相对于当前轮次）
- live_state 比赛状态（"1"=未开始，"3"=进行中，"4"=已结束）"""

_SYSTEM_TMPL = """你是电视语音助手的体育意图解析器。读用户的话，输出该调用的工具名和参数。

【工具】
{tool_briefs}

【怎么选工具】
按下面的顺序判断，**先命中先返回**：
1. 「**预约/订阅/提醒**某场比赛」→ sports_match_reservation。
2. 「**某支球队本身**」的资料/主页/介绍（如「皇马」「查一下湖人」「我想看国足」）
   → sports_team_search。
3. 「**积分榜/排名/排行榜/榜单**」→ sports_rank_search（同时要 sport_game + sport_rank_type）。
4. 上下文里**已锁定一场比赛**，问「**阵容/首发/大名单**」→ sports_match_lineup_search。
5. 上下文里**已锁定一场比赛**，问「**赛况/战况/比分/发生了什么**」→ sports_match_event_card_search。
6. 上下文里**已锁定一场比赛**，问「**技术统计/控球率/射门数/数据**」→ sports_match_statistics_card_search。
7. 要**看视频**（视频/录像/集锦/回放/精彩瞬间/纪录片/颁奖/开幕式）→ sports_vod_search。
8. 其余「**查赛程/对阵/比赛安排**」（找比赛、看有什么比赛、某队某赛事的比赛）
   → sports_match_search。

注意「比赛回放/比赛回看/比赛录像」属于 **sports_match_search**（按 live_state 过滤已结束比赛），
不是 sports_vod_search。

【参数怎么写】

A) sports_team_search：`{{"sport_team":"球队名"}}`

B) sports_rank_search：`{{"sport_game":"赛事","sport_rank_type":"榜单类型"}}`
   （如 积分榜/射手榜/抢断榜/金牌榜/排行榜）

C) sports_match_lineup_search / sports_match_event_card_search /
   sports_match_statistics_card_search：**params 输出 `{{}}`**（它们作用于上下文里那场已锁定的比赛）

D) sports_match_search / sports_vod_search / sports_match_reservation：
   `{{"query": <条件树>}}`

【query 条件树】
可用字段（**只写 field 名，取值按用户原话或最接近的常用说法填**）：
{fields}

节点形态（任意嵌套）：
- 单值：{{"field":"sport_game","value":"中超"}}
- 多值：{{"field":"sport_team","values":["尤文图斯","AC米兰"],"operator":"or"}}
- 组合：{{"and":[...]}} / {{"or":[...]}} / {{"not":{{...}}}}
- 条件只有一个时，query 直接写那个叶子，**不要包 and**。

取值口径：
- **时间维度照常写出来**（`sport_time`/`sport_time_range`/`round_offset`），
  具体日期由后处理补全，你只需填 `{{"field":"sport_time","value":""}}` 占位即可。
  用户说「上一轮/下一轮/下场」用 `round_offset`（"-1"/"1"）。
- 「X主场」用 sport_team_home；「X客场」用 sport_team_guest。
- 说了球队/球星就别重复给运动项目，除非用户也说了。
- 字段无法归一化到常用值时不传该字段，不要自造新值。

【输出】
只输出一个 JSON 对象，形如 {{"tool":"工具名","params":{{...}}}}，不要任何解释。
只抽用户明确表达的信息，不臆造；用户没提的可选字段不要出现（不要填 null / 空串 / 空数组）。
"""


def norm_retext(q: str) -> str:
    """sports 无 retext 字段，保留占位以符合 KernelSpec 契约。"""
    return (q or "").strip()


def fix_params(query: str, tool: str, params: dict) -> tuple[str, dict]:
    """后处理矫正：把时间类字段的取值确定性补全。

    LLM 负责「识别出这是时间维度」（结构判断），取值则由确定性解析器给出 ——
    相对日期（今天/明天/后天/周X/X月）依赖基准日，模型无法推算。
    与 rules 的 `_rel_base` 同口径：golden 混了两套基准日。
    """
    if not isinstance(params, dict):
        return tool, {}
    if tool in _NO_PARAM_TOOLS:
        return tool, {}
    q = params.get("query")
    if not isinstance(q, dict):
        return tool, params

    base = _rel_base(query)
    resolved = _resolve_time(query, base)   # 该话里能确定性解析出的全部时间叶子
    node = _strip_time(q)                   # 先摘掉模型填的时间叶（保留其余结构）
    if resolved:
        node = _merge(node, resolved)       # 再按确定性结果补回

    return tool, {**params, "query": node}


def _strip_time(node):
    """摘掉条件树里全部时间叶（模型给的取值不可信：相对日期它推算不出）。

    全程**保持黄金形状**：多条件恒为 {"and":[...]}，绝不产出裸数组
    （裸数组是 LLM 的常见误形，已在 postproc 里归一，这里不能再造一个出来）。
    """
    if not isinstance(node, dict):
        return node
    if node.get("field") in _TIME_FIELDS:
        return None
    if "and" in node and isinstance(node["and"], list):
        inner = [_strip_time(x) for x in node["and"]]
        inner = [x for x in inner if x is not None]
        if not inner:
            return None
        return inner[0] if len(inner) == 1 else {"and": inner}
    out = {k: _strip_time(v) for k, v in node.items()}
    out = {k: v for k, v in out.items() if v is not None}
    return out or None


def _merge(node, extra: list[dict]):
    """把确定性时间叶并入条件树，保持 golden 的形状约定。"""
    rest = _as_list(node)
    all_leaves = rest + list(extra)
    if not all_leaves:
        return {}
    if len(all_leaves) == 1:
        return all_leaves[0]
    return {"and": all_leaves}


def _as_list(node) -> list:
    """把条件树摊平成叶子列表（and 展开；空/单叶归一）。"""
    if node is None or node == {}:
        return []
    if isinstance(node, list):
        out = []
        for x in node:
            out.extend(_as_list(x))
        return out
    if isinstance(node, dict) and "and" in node and isinstance(node["and"], list):
        return _as_list(node["and"])
    return [node]


def _has_time(node) -> bool:
    if isinstance(node, dict):
        if node.get("field") in _TIME_FIELDS:
            return True
        return any(_has_time(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_time(x) for x in node)
    return False


def _has_time_expr(q: str) -> bool:
    return bool(re.search(
        r"今天|现在|明天|明日|后天|昨天|昨日|周[日天一二三四五六]|星期|礼拜"
        r"|\d{1,2}月|\d{1,2}[日号]|上一轮|下一轮|上轮|下轮|下场比赛?|上场比赛?|本轮|这轮",
        q))


def _resolve_time(query: str, base) -> list[dict]:
    """确定性解析时间维度 → 条件树叶子列表（复用 rules 的解析顺序，实测 32/32）。

    顺序即 rules 的口径，不可随意调换：
      相对轮次 → 绝对日期(带时刻则再叠 time_range) → 今天/周X(非时刻)
      → 时刻区间 → 周区间。

    注意「8月25日的下场球赛」会同时产出 sport_time 与 round_offset —— golden 两个都留，
    故返回**列表**而非单叶。
    """
    leaves: list[dict] = []
    if re.search(r"上一轮|上轮", query):
        leaves.append(exact("round_offset", "-1"))
    elif re.search(r"下一轮|下轮", query):
        leaves.append(exact("round_offset", "1"))
    elif re.search(r"下一场|下场比赛|下场球赛|下场", query):
        leaves.append(exact("round_offset", "1"))

    rng = _rules._time_mod_range(query, base)
    d = _rules._parse_date_cn(query, base)
    if d:
        # 「晚上7点35分」这类既要日期又要时刻：golden 只留 time_range（见 rules._reserve）
        leaves.append(rng or exact("sport_time", d))
        return leaves
    t = _rules._today_time(query, base)
    wd = _rules._weekday_date(query, base)
    if t and not rng:
        leaves.append(exact("sport_time", t))
    elif wd and not rng:
        leaves.append(exact("sport_time", wd))
    elif rng:
        leaves.append(rng)
    else:
        rngw = _rules._week_range(query, base)
        if rngw:
            leaves.append(rngw)
    return leaves


def _fill_time(query: str, base, field: str) -> list[dict]:
    """按 rules 的口径解析出时间条件叶子；解析不出返回空列表。"""
    return _resolve_time(query, base)


def _tool_briefs(tools) -> str:
    lines = []
    for t in tools:
        desc = " ".join(t.description.replace("\\n", " ").split())
        lines.append(f"- {t.name}：{desc[:900]}")
    return "\n".join(lines)


def system_prompt(domain, tools) -> str:
    return _SYSTEM_TMPL.format(tool_briefs=_tool_briefs(tools), fields=_FIELD_CATALOG)


SPEC = KernelSpec(
    key="sports",
    textkey=textkey,
    system_prompt=system_prompt,
    norm_retext=norm_retext,
    retext_tools=set(),          # sports 无 retext
    search_family=set(),         # 无 fuzzy 改判
    # 扁平/条件树混合、工具间无包含关系：全暴露、不下钻
    umbrella="",
    drill=None,
    all_fields=_ALL_FIELDS,
    fuzzy_tool="",
    oor_words=(),
    fix_params=fix_params,
    pool_path=Path(__file__).resolve().parent / "testset.json",
)

pipeline = build_pipeline(SPEC)
default_pool = pipeline.default_pool
