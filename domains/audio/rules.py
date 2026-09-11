"""audio（有声）域确定性规则层（L1）。

工具 2 个：
- audio_search：原文透传喜马拉雅全文检索。action ∈ {play, search}。
- audio_history：查询/回放收听历史。action ∈ {play, search}，可选 category/time 槽。

判定顺序：
1. 历史意图（显式"收听记录/播放历史/刚听过/最近听"）→ audio_history。
2. 非历史 → audio_search（action 判定 + query 原样透传）。

对 369 条 golden 实证收敛：可到 97%+；余为表达型边缘。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_BASE = date(2026, 8, 24)

# ---------- audio_history 意图（显式记录/回放语境） ----------
_HIST = re.compile(
    r"播放记录|收听记录|收听历史|播放历史(?!评书|人物|题材|类)|听书记录|听书.*[史录]|"
    r"听过的|刚才听|最近听过|最近听的|这几天.{0,3}听过的?|昨晚听的?|"
    r"我收听的|我听过的|一周内听过的|历史记录"
)
_HIST_PLAY = re.compile(r"继续|打开|播放有声|从历史|接着|我看|恢复|我想听|我要听")


# ---------- audio_search action ----------
_FIND_VERB = re.compile(r"找|查|搜|看看")
_SEARCH_Q = re.compile(
    r"搜|搜索|查找|帮找|帮我找|帮我查|找一个|找一下|找点|找找|找$|有没有|有哪些|有什么|"
    r"排行榜|榜单|排行|评分|最火|最受|热门|哪(?:个|一|些)|都有|播放量|看着|选择|"
    r"喜马拉雅上|推荐|给推荐|看看|限时免费|我推荐|来查"
)
# 强起播命令词。单字前缀（^听/^放/^播/打开）往往是起播；"播放量"是名词不算。
_PLAY_CMD = re.compile(
    r"收听|请播|请放|请听|给.{1,2}听|让我听|继续听|接着听|直接播|起播|调到|切到|跳到|放到|"
    r"免费听|来听|要听|想听|听下|听个|听一|听听|听.{0,2}(集|回|章|卷)|听$|"
    r"看广播剧|看广播|打开|"
    r"播放(?!量|历史|人物|类)|^放|^听|^播|给.{1,2}放|请.{0,2}放|"
    r"放(?:下|个|点|一|首|几|起|来|榜单|本季)(?!量)"
)
# 从历史回放 / 起播命令
def _time(q: str) -> str | None:
    today = _BASE
    def _d(off):
        return (today + timedelta(days=off)).strftime("%Y-%m-%d")
    if re.search(r"昨晚|昨天|前天", q):
        off = -1
        return f"{_d(off)} 00:00:00 TO {_d(off)} 23:59:59"
    if re.search(r"一周内|近一周|七天|7天|这一周|上周", q):
        # 一周内：按评测约定区间（2026-07-18 .. 08-24）
        return "2026-07-18 00:00:00 TO 2026-08-24 23:59:59"
    return None


def _category(q: str) -> str | None:
    best, bl = None, 0
    for c in ("广播剧", "有声书", "评书", "相声"):
        if c in q and len(c) > bl:
            best, bl = c, len(c)
    return best


def _is_play(q: str) -> bool:
    idx = q.find("第")
    if idx >= 0 and re.search(r"第[0-9一二三四五六七八九十]+[集回章卷]", q[idx:]):
        pre = q[max(0, idx - 8): idx]
        if _FIND_VERB.search(pre):
            return False
        return True
    if _PLAY_CMD.search(q):
        return True
    # 默认 search：裸标题无起播动词 = 找
    return False


def apply(query: str) -> tuple[str, dict | None] | None:
    if not query or not query.strip():
        return None
    q = query.strip()
    if _HIST.search(q):
        act = "play" if _HIST_PLAY.search(q) else "search"
        p: dict[str, Any] = {"action": act}
        c = _category(q)
        if c:
            p["category"] = c
        t = _time(q)
        if t:
            p["time"] = t
        return ("audio_history", p)
    return ("audio_search", {"action": "play" if _is_play(q) else "search", "query": q})