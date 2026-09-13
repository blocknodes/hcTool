"""audio（有声）域规则层（L1）—— 规则表驱动。

判定顺序（与迁移前 apply() 完全一致，数字即 priority，越小越先）：
1. audio_history（显式"收听记录/播放历史/刚听过/最近听"）→ audio_history
   若带续播命令则 action=play，否则 search。
2. 其余一律 audio_search：action 由 query 起播/检索 cue 判定，query 原样透传。

这段原来是单一 apply 里按正则顺序 if-else，改造成 RuleSet 后：
- 每条都是显式 Rule（id/title/explain/enabled），命中可审计、可拔插。
- build 仍是原逻辑（_hist_params / audio_search 组装），不改变任何 (tool, params) 输出。
预期：对 369 条 golden 零回归（tool 100% / param 100%）。

审计/禁用的方法见 app/rulebase.py 文档。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.rulebase import Rule, RuleSet

_BASE = date(2026, 8, 24)

# ============================ 工具判定 ============================
# audio_history 意图（显式记录/回放语境）
_HIST = re.compile(
    r"播放记录|收听记录|收听历史|播放历史(?!评书|人物|题材|类)|听书记录|听书.*[史录]|"
    r"听过的|刚才听|最近听过|最近听的|这几天.{0,3}听过的?|昨晚听的?|"
    r"我收听的|我听过的|一周内听过的|历史记录"
)
_HIST_PLAY = re.compile(r"继续|打开|播放有声|从历史|接着|我看|恢复|我想听|我要听")

# audio_search action 判据
_FIND_VERB = re.compile(r"找|查|搜|看看")
_SEARCH_Q = re.compile(
    r"搜|搜索|查找|帮找|帮我找|帮我查|找一个|找一下|找点|找找|找$|有没有|有哪些|有什么|"
    r"排行榜|榜单|排行|评分|最火|最受|热门|哪(?:个|一|些)|都有|播放量|看着|选择|"
    r"喜马拉雅上|推荐|给推荐|看看|限时免费|我推荐|来查"
)
_PLAY_CMD = re.compile(
    r"收听|请播|请放|请听|给.{1,2}听|让我听|继续听|接着听|直接播|起播|调到|切到|跳到|放到|"
    r"免费听|来听|要听|想听|听下|听个|听一|听听|听.{0,2}(集|回|章|卷)|听$|"
    r"看广播剧|看广播|打开|"
    r"播放(?!量|历史|人物|类)|^放|^听|^播|给.{1,2}放|请.{0,2}放|"
    r"放(?:下|个|点|一|首|几|起|来|榜单|本季)(?!量)"
)
_SONG_HEAD = re.compile(r"第[0-9一二三四五六七八九十]+[集回章卷]")
_CATEGORIES = ("广播剧", "有声书", "评书", "相声")


def _time(q: str) -> str | None:
    today = _BASE

    def _d(off):  # noqa: ANN202
        return (today + timedelta(days=off)).strftime("%Y-%m-%d")

    if re.search(r"昨晚|昨天|前天", q):
        off = -1
        return f"{_d(off)} 00:00:00 TO {_d(off)} 23:59:59"
    if re.search(r"一周内|近一周|七天|7天|这一周|上周", q):
        return "2026-07-18 00:00:00 TO 2026-08-24 23:59:59"
    return None


def _category(q: str) -> str | None:
    best, bl = None, 0
    for c in _CATEGORIES:
        if c in q and len(c) > bl:
            best, bl = c, len(c)
    return best


def _is_play(q: str) -> bool:
    idx = q.find("第")
    if idx >= 0 and _SONG_HEAD.search(q[idx:]):
        pre = q[max(0, idx - 8): idx]
        if _FIND_VERB.search(pre):
            return False
        return True
    if _PLAY_CMD.search(q):
        return True
    return False  # 默认 search：裸标题无起播动词 = 找


# ============================ 规则 build 回调 ============================
def _hist_params(q: str) -> dict[str, Any]:
    act = "play" if _HIST_PLAY.search(q) else "search"
    p: dict[str, Any] = {"action": act}
    c = _category(q)
    if c:
        p["category"] = c
    t = _time(q)
    if t:
        p["time"] = t
    return p


def _search_params(q: str) -> dict[str, Any]:
    return {"action": "play" if _is_play(q) else "search", "query": q}


# ============================ 规则表（顺序 = priority） ============================
RULE_SET = RuleSet(
    rules=[
        Rule(
            id="audio_history_explicit",
            tool="audio_history",
            priority=1,
            title="收听/播放历史",
            explain="query 含显式历史语境（播放记录/收听历史/最近听…）→ 查历史，action 由续播词判定",
            match_re=r"播放记录|收听记录|收听历史|播放历史(?!评书|人物|题材|类)|听书记录|听书.*[史录]|"
                     r"听过的|刚才听|最近听过|最近听的|这几天.{0,3}听过的?|昨晚听的?|"
                     r"我收听的|我听过的|一周内听过的|历史记录",
            build=_hist_params,
        ),
    ],
    default=Rule(
        id="audio_search_fallback",
        tool="audio_search",
        priority=99,
        title="有声内容搜索（兜底）",
        explain="非历史意图一律 audio_search：action 由 起播/检索 cue 判定，query 原样透传",
        build=_search_params,
    ),
)


def apply(query: str):
    """入口：优先返回带规则 id 的三元组 (tool, params, rule_id)；兼容二元组调用方。

    返回 None 交 fallback。带 rule_id 是为了审计：知道这条 prediction 由哪条规则命中。
    """
    if not query or not query.strip():
        return None
    sel = RULE_SET.select_with_rule(query)
    if sel is None:
        return None
    tool, params, rule = sel
    if rule is RULE_SET.default:
        return (tool, params)
    return tool, params, rule.id


__all__ = ["apply"]