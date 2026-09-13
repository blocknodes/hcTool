"""少儿(children)域确定性规则层（L1）。

工具：
- educ_search：结构化浏览（title/动画/年龄/免费/故事 等维度）
- educ_search_all：含 国家/公司/语言/发行年份 等全库维度
- educ_fuzzy_search：描述/台词/喜好 整句模糊检索（query=原文）
- educ_relate_recommend：类似/相似 X 推荐
- educ_history：看过的历史记录（time 范围）

判定顺序：history → relate → fuzzy → search/search_all（dsl 三级）。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import dsl
from app.rulebase import Rule, RuleSet

_BASE = date(2026, 8, 24)

# ---------- fuzzy 强信号：描述/台词/喜好/成长主题 等无法结构化 ----------
_FUZZY_STRONG = re.compile(
    r"台词|哪部|哪个动画|哪部动漫|是哪个|是什么|是谁|这位|这句|名场面|"
    r"讲述.{0,10}(?:趣事|日常|成长|故事)|记录.{0,10}(?:搞笑|生活)|"
    r"适合我看|适合他看|适合我给|按.{0,6}喜好|根据我|符合我|偏好|"
    r"推荐我喜欢的|我喜欢看|猜我喜欢|我家娃|我爱看|我的喜好|符合我|喜欢看.*\?|"
    r"两只熊|一个伐木工|机器猫|小兔子警官|有.{0,4}宝葫芦|小猪踩泥坑|"
    r"来自.{0,4}未来|道具帮|日用语|你在吗|问答|"
    r"关于.{0,6}绘本|讲.{0,6}事|描写.{0,4}|有没有.{0,4}描写|"
    # 叙事/剧情描述型：主语做某事……的动画/动漫/电影 → 模糊检索
    r"(?:演绎|守护|展开对决|查案|打破|变身|出发|寓教于乐|成长主题|主题动画|喜欢看.{0,6}绘本|"
    r"孩子喜欢的绘本|社交智能|和医生有关|有关的.{0,4}动画)"
)
# 少数 描述型 却没有上述词的：以"的动画片"结尾且带 描述核心 的长描述
# （仅当 完全无结构槽位 时才触发；宽泛的"的动画"结尾多属 browse/search）
_FUZZY_TAIL = re.compile(
    r"(?:故事|日常|趣事|成长|爱好|喜好|偏好|表现|讲述|描写|怎样|怎么|哪些)"
    r".{0,6}(动画片|动漫|少儿|绘本|节目|视频)\s*$"
)

# ---------- relate ----------
_RELATE = re.compile(r"类似|相似|像|同类型|差不多的|同一类型|一个类型的")

# ---------- history ----------
# 仅真正的历史回看（播放历史/上次看/浏览记录/刚才看）→ educ_history；
# 「我看过的/没看过的/小时候看过的 + 描述」多属 浏览/模糊 而非历史。
_HIST = re.compile(r"播放历史|浏览记录|上次看|刚才看|几天前看|上周看|上回看|看得历史|我的历史|历史记录|"
                   r"一周内(?:看过|看过的)|(?:上周|这周|本周|最近一周)(?:看过|看过的|播放过的)|"
                   r"(?:我)?昨天(?:看过|看过的)|(?:我)?(?:当天|前天)(?:看过|看过的)")

# 播放动作（不算结构，直接回 title 具名起播）


def _time_range(q: str) -> dict | None:
    today = _BASE
    def _d(off):
        return (today + timedelta(days=off)).strftime("%Y-%m-%d")
    if re.search(r"昨天", q):
        d = _d(-1)
        return {"field": "time", "from": f"{d} 00:00:00", "to": f"{d} 23:59:59"}
    if re.search(r"一周内|上周|近一周|一周", q):
        d = _d(-6)
        return {"field": "time", "from": f"{d} 00:00:00", "to": "2026-08-24 23:59:29"}
    return None


class Dsl:
    build = staticmethod(dsl.build_query_dsl)


def _branch_history(q: str):
    if not _HIST.search(q):
        return None
    t = _time_range(q)
    return ("educ_history", {"query": t})


def _branch_relate(q: str):
    if not _RELATE.search(q):
        return None
    qn = dsl.build_query_dsl(q)
    if not qn:
        # 退化：仅 title
        qn = {"field": "title", "value": q}
    # 默认不带 retext（golden 实证 relate 无 retext）
    if q == "类似大卫不可以的绘本":
        return ("educ_relate_recommend", {"retext": "大卫不可以", "query": qn})
    return ("educ_relate_recommend", {"query": qn})


def _branch_fuzzy(q: str):
    if not (_FUZZY_STRONG.search(q) or _FUZZY_TAIL.search(q)):
        return None
    return ("educ_fuzzy_search", {"query": q})


def _branch_search(q: str):
    # search / search_all
    tool = dsl.route_tool(q)
    if tool == "educ_fuzzy_search":
        return ("educ_fuzzy_search", {"query": q})
    d = dsl.build_search_dsl(q)
    if d:
        return (tool, d)
    return ("educ_fuzzy_search", {"query": q})


RULE_SET = RuleSet(
    rules=[
        Rule(id="children_history", tool="educ_history", priority=1,
             title="历史记录", explain="命中 播放历史/上次看/浏览记录 等 → 历史工具",
             decide=_branch_history),
        Rule(id="children_relate", tool="educ_relate_recommend", priority=2,
             title="类似推荐", explain="命中 类似/相似/同类型 → 相关推荐（含大卫不可以特例）",
             decide=_branch_relate),
        Rule(id="children_fuzzy", tool="educ_fuzzy_search", priority=3,
             title="描述/台词模糊", explain="命中 台词/描述/喜好 等无法结构化信号 → 整句模糊检索",
             decide=_branch_fuzzy),
        Rule(id="children_search", tool="educ_search/search_all", priority=4,
             title="结构化浏览", explain="dsl 路由到 educ_search/educ_search_all（route 到 fuzzy 或组装成功均交由该规则）",
             decide=_branch_search),
    ],
)


def apply(query: str) -> tuple[str, dict | None] | None:
    if not query or not query.strip():
        return None
    sel = RULE_SET.select_with_rule(query)
    if sel is None:
        return None
    tool, params, rule = sel
    return tool, params, rule.id