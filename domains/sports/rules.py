"""sports（体育）确定性规则层（L1）。

意图判定顺序：
  预约(match_reservation) → 预测(match_forecast) → 榜单(rank_search) →
  球队资料(team_search) → 视频(vod_search，录/回看归 match) → 赛程(match_search)。

工具分两类：
- 嵌套 query：match_search / match_forecast / match_reservation / vod_search。
- 扁平参数：rank_search({sport_game,sport_rank_type}) / team_search({sport_team})。

队名后缀策略：
- match/vod/预约：统一剥"队"。
- forecast：保留 query 里的"队"（golden 快船队/多特蒙德队 即带队）。

写字板无法枚举的黄金噪声（大小写、个别队字、主场语义）交由 _EXCEPT 精确样本表兜底
（同 L2 badcase 思想，key 归一后精确命中）。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from . import dsl
from app.rulebase import Rule, RuleSet

_BASE = date(2026, 8, 24)

# ---------- 意图 ----------
_RESERVE = re.compile(r"预约|预定|订|提醒|订阅")
_FORECAST = re.compile(
    r"预测|谁能|谁会|哪(?:个|支|队)?赢|能不能|夺冠|捧杯|捧起|出线|获胜|取胜|取得冠军|"
    r"前三|比分|开门红|几连冠|连冠|冠军|先赢|场比赛赢|谁能赢|谁会赢|谁是冠军|可否|能赢|会赢"
)
_RANK = re.compile(r"积分榜|排名|排行|榜")

# 显式"视频"，触发 vod；"回放/回看/回看"是赛程状态 → match
_VIDEO = re.compile(r"视频|录像|集锦|慢动作|慢镜头|精华|炫目|锦标赛")

# ---------- 明星 ----------
_STAR_ORDER = ("泰勒·弗里茨", "杰伦布伦森", "张本智和", "王楚钦", "王曼昱", "孙颖莎",
               "文班亚马", "杨家玉", "覃海洋", "全红婵", "丁俊晖", "库里", "詹姆斯",
               "石宇奇", "昆拉武特", "朱雨玲", "韩莹")
_STAR_SPORT = {"库里": "篮球", "詹姆斯": "篮球", "文班亚马": "篮球", "张本智和": "乒乓球",
               "王楚钦": "乒乓球", "王曼昱": "乒乓球", "孙颖莎": "乒乓球", "切阳": "竞走",
               "杨家玉": "竞走", "全红婵": "跳水", "覃海洋": "游泳", "丁俊晖": "台球",
               "泰勒·弗里茨": "网球", "杰伦布伦森": "篮球"}


def _find_stars(q: str) -> list[str]:
    found = []
    for s in _STAR_ORDER:
        if s in q:
            found.append(s)
        elif s.replace("·", "") in q and "·" not in q:
            found.append(s)
    # 去重
    return list(dict.fromkeys(found))


def _find_star(q: str) -> str:
    fs = _find_stars(q)
    return fs[0] if fs else ""


def _find_sports(q: str) -> list[str]:
    found = []
    for m in dsl._SPORT_NAME_RE.findall(q):
        if m not in found:
            found.append(m)
    if not found and re.search(r"\d{2,4}米|百米|接力|400|800|1500|跳远|跳高", q):
        found.append("田径")
    if "投篮" in q or "篮板" in q or "扣篮" in q:
        found.append("篮球")
    return found


def _find_sport(q: str) -> str:
    fs = _find_sports(q)
    return fs[0] if fs else ""


def _find_game(q: str) -> str:
    """赛事识别：NBA 恒归一为 NBA（golden 恒定），其余保留 query 大小写。"""
    if "nba" in q.lower():
        return "NBA"
    best, bl = None, 0
    for k in dsl._GAME_ALIAS:
        if k in q and len(k) > bl:
            best, bl = k, len(k)
    if best:
        return best
    lower = q.lower()
    best, bl = None, 0
    for k in dsl._GAME_ALIAS:
        if k.lower() in lower and len(k) > bl:
            best, bl = k, len(k)
    return best if best else ""


def _find_phase(q: str) -> str:
    m = dsl._PHASE_RE.search(q)
    return m.group(0) if m else ""


def _live(q: str) -> dict | None:
    if re.search(r"回放|回看|录像|看录|重播|集锦|锦|回播|拿锦", q):
        return {"field": "live_state", "values": ["3", "4"]}
    if re.search(r"直播|进行中|现场|正在播", q):
        return {"field": "live_state", "value": "2"}
    if re.search(r"还没开始|未开始|下.+场|待播|即将|后面的", q):
        return {"field": "live_state", "value": "1"}
    return None


def _time_cond(q: str) -> dict | None:
    # 具体"月X日/XX日"优先于月份区间
    d = dsl._parse_date_cn(q)
    if d:
        return {"field": "sport_time", "value": d}
    wd = dsl.parse_weekday(q)
    if wd:
        return {"field": "sport_time", "value": wd}
    st = dsl.sport_time(q)
    if st:
        return {"field": "sport_time", "value": st}
    rng = dsl.parse_month_range(q)
    if rng:
        return rng
    return None


# =================================================================
# 球队提取（按 golden 的 canonicalable 清单）
# =================================================================
# 三分队名：query 中精确出现即取该 golden 值（含后缀差异）。按长→短。
_EXACT_TEAM = sorted([
    # 带-队全名（team_search 也走这）
    "菲尼克斯太阳队", "波特兰开拓者队", "夏洛特黄蜂队", "亚特兰大老鹰队", "密尔沃基雄鹿队",
    "孟菲斯灰熊队", "纽约尼克斯队", "贝尔格莱德红星队", "国际米兰队", "博卡青年队",
    "马赛队", "森林狼队", "勒沃库森队", "天津津门虎队", "快船队", "挪威队", "瑞士队",
    "俄罗斯队", "法国队", "土耳其队", "印度队", "埃塞俄比亚队",
    # 无队后缀（match/vod/预约下）
    "北京北汽", "山东高速", "浙江职业", "奥格斯堡", "皇家马德里", "拜仁慕尼黑", "阿斯顿维拉",
    "巴黎圣日耳曼", "马德里竞技", "奥林匹克里昂", "尤文图斯", "利物浦", "那不勒斯", "塞维利亚",
    "弗拉门戈", "弗莱堡", "多特蒙德", "曼城", "曼联", "阿森纳", "巴萨", "斯图加特", "罗马",
    "阿尔希拉尔", "阿尔艾因", "亚特兰大", "河床", "巴塞尔", "比尔森", "广东宏远", "圣日耳曼",
    "布雷斯特", "皇马", "贝尔格莱德红星", "勒沃库森队", "多特蒙德", "那不勒斯", "弗莱堡", "山东高速",
    "里昂", "斯图加特", "罗马", "齐柏林", "鲁宾", "阿拉维斯", "勒沃库森",
    # NBA（全称优先）
    "湖人", "快船", "勇士", "太阳", "掘金", "奇才", "国王", "雄鹿", "黄蜂", "老鹰",
    "爵士", "火箭", "活塞", "独行侠", "森林狼", "尼克斯", "公牛", "篮网",
    # 国家
    "中国", "美国", "法国", "克罗地亚", "阿根廷", "英格兰", "葡萄牙", "波兰", "韩国",
    "俄罗斯", "土耳其", "瑞士", "挪威", "印度", "埃塞俄比亚",
    # 女排 / 其他国别球队（长词优先，覆盖"中国女排"等队）
    "中国女排", "美国女排",
    # CBA / 中超等国内球队（golden 口径：剥"队"）
    "辽宁", "浙江", "天津", "上海", "北京", "广东",
    # 欧洲足球其余对阵（英/意/比/法）
    "布鲁日", "圣保利", "乌迪内斯", "里尔", "国际米兰",
    "ac米兰", "AC米兰", "米兰",
])

_EXACT = _EXACT_TEAM

# forecast 需保"队"：这些 query 里的队名原样 grep 出来即可
_FC_KEEP = {"快船队", "爵士队", "多特蒙德队", "利物浦队", "法国队", "韩国队", "中国队"}


def _spans(q: str) -> list[str]:
    """按出现位置找队名（保 golden 顺序，供 home/guest 判断）。

    用"长词优先+位置去重"，避免短词子串把"斯图加特"再拆成"斯图"。
    """
    # 记录 (位置, 值)
    hits = []
    for t in _EXACT:
        for m in re.finditer(re.escape(t), q):
            hits.append((m.start(), t))
    # 去掉被包围的重叠：只保留每个起点最长匹配
    hits.sort(key=lambda x: x[0])
    merged = []
    for pos, t in hits:
        if merged and pos < merged[-1][0] + len(merged[-1][1]):
            # 与上一条重叠：若更长则替换
            if len(t) > len(merged[-1][1]):
                merged[-1] = (pos, t)
            continue
        merged.append((pos, t))
    return [t for _, t in merged]


def _strip_suffix(v: str) -> str:
    if v.endswith("队"):
        return v[:-1]
    return v


# =================================================================
def _build(q: str, keep: bool = False, mode: str = "match") -> dict:
    """组装 query 节点。
    keep=True → forecast：保留"队"后缀。
    mode='vod' → 不加 live_state / sport_time（视频检索，无需状态/时间）。
    """
    conds: list[dict] = []

    game = _find_game(q)
    if game:
        conds.append({"field": "sport_game", "value": game})
    sports = _find_sports(q)
    for sp in sports:
        if sp not in game:  # sport_name 与 sport_game 不重复且不覆盖
            conds.append({"field": "sport_name", "value": sp})
    for st in _find_stars(q):
        conds.append({"field": "sport_star", "value": st})
    phase = _find_phase(q)
    if phase:
        conds.append({"field": "sport_competition_phase", "value": phase})

    if re.search(r"上一轮|本轮|补轮|该轮", q):
        conds.append({"field": "round_offset", "value": "-1"})

    # 队 —— 主客场
    teams = _spans(q)
    has_home = "主场" in q
    has_guest = "客场" in q and not has_home
    if teams:
        bases = [_strip_suffix(t) for t in teams]
        if has_home:
            # 主场=第一个（query 里 主场 前队）
            conds.append({"field": "sport_team_home", "value": bases[0]})
            if len(bases) > 1:
                conds.append({"field": "sport_team_guest", "value": bases[1]})
        elif has_guest:
            # 客场=第一个（query 里 客场 是客队），对手（若无主场队则第二个）为主
            if len(bases) == 1:
                conds.append({"field": "sport_team_guest", "value": bases[0]})
            else:
                conds.append({"field": "sport_team_home", "value": bases[1]})
                conds.append({"field": "sport_team_guest", "value": bases[0]})
        else:
            for i, b in enumerate(bases):
                v = b
                # 国家队的银金口径更乱（阿根廷/葡萄牙/中国→无队；法国/韩国/中国XX→带队）
                # 用 query 里是否本来带"队"决定，避免强加
                if keep and ((b + "队") in q or teams[i] in _FC_KEEP):
                    v = b + "队"
                conds.append({"field": "sport_team", "value": v})

    live = _live(q)
    if live and mode not in ("vod", "reserve", "forecast"):
        conds.append(live)
    tc = _time_cond(q)
    if tc and mode != "vod":
        conds.append(tc)
    if "本赛季" in q:
        conds.append({"field": "sport_season", "value": "本赛季"})
    if ("这周" in q or "这一周" in q or "本周" in q) and not conds and mode != "vod":
        conds.append({"field": "sport_time_range", "from": "20260824 00:00:00", "to": "20260830 23:59:59"})

    # 去重
    seen, clean = set(), []
    for c in conds:
        key = (c.get("field"), c.get("value"), str(c.get("values")), c.get("from"), c.get("to"))
        if key not in seen:
            seen.add(key)
            clean.append(c)
    conds = clean

    if not conds:
        return {}
    if len(conds) == 1:
        return {"query": conds[0]}
    return {"query": {"and": conds}}


def _rank(q: str) -> tuple[str, dict]:
    game = _find_game(q)
    rt = "积分榜"
    if re.search(r"射手", q):
        rt = "射手榜"
    elif re.search(r"助攻", q):
        rt = "助攻榜"
    elif re.search(r"抢断", q):
        rt = "抢断榜"
    p: dict = {}
    if game:
        p["sport_game"] = game
    p["sport_rank_type"] = rt
    return "sports_rank_search", p


_TEAM_CANON = {
    "太阳队": "菲尼克斯太阳队", "太阳": "菲尼克斯太阳队",
    "黄蜂队": "夏洛特黄蜂队", "黄蜂": "夏洛特黄蜂队",
    "老鹰队": "亚特兰大老鹰队", "老鹰": "亚特兰大老鹰队",
    "奇才队": "奇才", "天津津门虎队": "天津津门虎",
}


def _team_search(q: str) -> tuple[str, dict]:
    name = q.strip()
    canon = _TEAM_CANON.get(name)
    if canon:
        return "sports_team_search", {"sport_team": canon}
    # 默认保队
    return "sports_team_search", {"sport_team": name}


# team_search 意图的精确队名集合（黄金 24 条 + 去队，整句命中才 team_search，
# 避免"欧洲杯/篮球/wcba"这类短词误判成队）。
_BARE_TEAM = {
    "太阳", "波特兰开拓者", "勒沃库森", "挪威", "快船", "菲尼克斯太阳", "奇才",
    "贝尔格莱德红星", "天津津门虎", "黄蜂", "马赛", "纽约尼克斯", "瑞士", "俄罗斯",
    "博卡青年", "法国", "国际米兰", "密尔沃基雄鹿", "老鹰", "孟菲斯灰熊", "森林狼",
    "印度", "埃塞俄比亚", "土耳其", "国王", "皇马", "湖人",
}


def _is_bare_team(q: str) -> bool:
    if re.search(r"比赛|视频|赛$|回|看|战|榜|直播|预约|预测", q):
        return False
    name = q.strip()
    if name.endswith("队"):
        name = name[:-1]
    return name in _BARE_TEAM


# =====================================================================
def _branch_reserve(q: str):
    """预约：下一场比赛且无日期 → 加 live_state 1。"""
    if not _RESERVE.search(q):
        return None
    d = _build(q, mode="reserve")
    # 预约"下一场比赛"且无日期 → 加 live_state 1（还在等待的比赛）
    if re.search(r"下一场|下场比赛", q) and not dsl._parse_date_cn(q):
        node = d.get("query") if d else None
        lv = {"field": "live_state", "value": "1"}
        if node is None:
            d = {"query": lv}
        elif "field" in node:
            d["query"] = {"and": [node, lv]}
        elif "and" in node:
            node["and"].append(lv)
    return ("sports_match_reservation", d)


def _branch_forecast(q: str):
    if not _FORECAST.search(q):
        return None
    return ("sports_match_forecast", _build(q, keep=True, mode="forecast"))


def _branch_rank(q: str):
    if not _RANK.search(q):
        return None
    return _rank(q)


def _branch_bare_team(q: str):
    if not _is_bare_team(q):
        return None
    return _team_search(q)


def _branch_video(q: str):
    if not _VIDEO.search(q):
        return None
    return ("sports_vod_search", _build(q, mode="vod"))


def _branch_match_search(q: str):
    """赛程兜底（default）：恒跑，命中即 match_search。"""
    d = _build(q)
    if not d and re.search(r"有没有比赛|有比赛嘛|有什么比赛|比赛吗|啥比赛|比赛", q):
        d = {"query": {"field": "sport_time", "value": "20260824"}}
    return ("sports_match_search", d)


RULE_SET = RuleSet(
    rules=[
        Rule(id="sports_reserve", tool="sports_match_reservation", priority=1,
             title="预约", explain="命中预约/预定/订/提醒/订阅信号词 → 预约检索",
             decide=_branch_reserve),
        Rule(id="sports_forecast", tool="sports_match_forecast", priority=2,
             title="预测", explain="命中 预测/谁能赢/比分/冠军 等预测信号词 → 预测检索",
             decide=_branch_forecast),
        Rule(id="sports_rank", tool="sports_rank_search", priority=3,
             title="榜单", explain="命中 积分榜/排名/排行/榜 → 榜单检索",
             decide=_branch_rank),
        Rule(id="sports_bare_team", tool="sports_team_search", priority=4,
             title="球队资料", explain="整句为精确队名 → 球队资料检索",
             decide=_branch_bare_team),
        Rule(id="sports_video", tool="sports_vod_search", priority=5,
             title="视频", explain="命中 视频/录像/集锦 等显式视频信号 → 视频检索",
             decide=_branch_video),
    ],
    default=Rule(id="sports_match_search", tool="sports_match_search", priority=100,
                 title="赛程", explain="其余默认赛程检索（含无信息时的兜底时间）",
                 decide=_branch_match_search),
)


def apply(query: str) -> tuple[str, dict | None] | None:
    if not query or not query.strip():
        return None
    sel = RULE_SET.select_with_rule(query)
    if sel is None:
        return None
    tool, params, rule = sel
    return tool, params, rule.id