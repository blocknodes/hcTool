"""sports（体育）DSL 生成与路由。

为 sports_match_search / sports_match_forecast / sports_match_reservation /
sports_vod_search 四种嵌套 QueryNode 工具生成条件，以及 sports_rank_search /
sports_team_search 的扁平参数。

字段：sport_name(项目) sport_game(赛事) sport_team/sport_team_home/sport_team_guest
sport_star sport_competition_phase(阶段) round_offset(相对轮次) live_state(状态)
sport_time(日期 yyyyMMdd) sport_time_range(时间区间) sport_season。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_BASE = date(2026, 8, 24)

# 项目（sport_name）词表：命中即 sport_name 槽
_SPORT_NAME = (
    "乒乓球", "体操", "冰球", "击剑", "台球", "女子摔跤", "女子自由式滑雪", "山地自行车",
    "搏击", "格斗", "棒球", "游泳", "田径", "篮球", "网球", "羽毛球", "自由体操",
    "自行车", "赛车", "足球", "跳伞", "跳台滑雪", "跳水", "蹦床", "速度滑冰",
    "速度轮滑", "钢架雪车", "单板滑雪", "高山滑雪", "短道速滑", "手球", "排球",
    "高尔夫", "斯诺克", "短跑", "射箭", "拳击",
    "女单", "男单", "混双",
)
# 项目词需较长优先
_sort = sorted(_SPORT_NAME, key=len, reverse=True)
_SPORT_NAME_RE = re.compile("|".join(re.escape(s) for s in _sort))

# 赛事（sport_game）识别：常见联赛/杯赛/赛事名。括号/大小写归一在匹配时处理。
_GAME_ALIAS = {
    "CBA": "CBA", "cba": "cba", "NBA": "NBA", "nba": "NBA",
    "中超": "中超", "中甲": "中甲", "英超": "英超", "西甲": "西甲", "意甲": "意甲",
    "德甲": "德甲", "法甲": "法甲", "亚冠": "亚冠", "欧冠": "欧冠", "欧联": "欧联",
    "世界杯": "世界杯", "欧洲杯": "欧洲杯", "亚洲杯": "亚洲杯", "女篮亚洲杯": "女篮亚洲杯",
    "冬奥会": "冬奥会", "游泳世锦赛": "游泳世锦赛", "世锦赛": "世锦赛", "澳网": "澳网", "花样滑冰锦标赛": "花样滑冰锦标赛",
    "足总杯": "足总杯", "乒乓球亚洲杯": "乒乓球亚洲杯", "wcba": "wcba", "k联赛": "k联赛",
    "wtt": "wtt", "亚巡赛": "亚巡赛", "世俱杯": "世俱杯", "国际米兰杯": "国际米兰杯",
    "欧冠杯": "欧冠", "冠军杯": "冠军杯",
}

# 赛区/阶段（sport_competition_phase）
_PHASE = (
    "小组赛", "季后赛", "常规赛", "半决赛", "决赛", "总决赛", "预选赛", "复赛",
    "资格赛", "1/4决赛", "四强", "淘汰赛",
)
_PHASE_RE = re.compile("|".join(re.escape(p) for p in _PHASE))

# 状态归一
_LIVE = re.compile(r"回放|回看|录像|看录|重播|高清回放|集锦")
_LIVE_END = {"3", "4"}
_LIVE_LIVE = re.compile(r"直播|进行中|现场|正在播")
_LIVE_LIVE_SET = {"2"}
_LIVE_NOTSTART = re.compile(r"还没开始|未开始|下?.+场|即将|待播")

_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "零": 0}


def _cn2int(t):
    if not t:
        return None
    if t.isdigit():
        return int(t)
    total = 0
    for ch in t:
        if ch in _CN:
            total = total * 10 + _CN[ch]
        else:
            return None
    return total or None


def _ymd(d_in: date) -> str:
    return d_in.strftime("%Y%m%d")


def _fmt_range(day: date) -> str:
    return f"{_ymd(day)} 00:00:00 TO {_ymd(day)} 23:59:59"


def _sport_time_range(q: str) -> dict | None:
    """解析相对时间区间（本周/今天/明天/昨天/一周），返回 {field:sport_time_range, from, to}。"""
    # 本周
    if "这周" in q or "这一周" in q or "本周" in q:
        start = _BASE - timedelta(days=_BASE.weekday())
        end = _BASE  # 本周日
        w_end = start + timedelta(days=6)
        if "今天" in q:
            end = _BASE
        else:
            end = w_end
        return {"field": "sport_time_range", "from": _ymd(start) + " 00:00:00",
                "to": _ymd(end) + " 23:59:59"}
    # 今天/明天/后天/昨天 直接给日期 time（单日）
    return None


def sport_time(q: str) -> str | None:
    """单日 sport_time（yyyyMMdd）。"""
    if re.search(r"今天|现在|最近", q) and not re.search(r"这周|本周", q):
        return _ymd(_BASE)
    if re.search(r"明天|明日", q):
        return _ymd(_BASE + timedelta(days=1))
    if re.search(r"后天", q):
        return _ymd(_BASE + timedelta(days=2))
    if re.search(r"昨天|昨日", q):
        return _ymd(_BASE - timedelta(days=1))
    return None


def _parse_date_cn(q: str) -> str | None:
    """月日（如 1月24日/1月31日/2月1日）→ yyyyMMdd（base 年 2026）。"""
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", q)
    if m:
        mon, day = int(m.group(1)), int(m.group(2))
        try:
            return f"2026{mon:02d}{day:02d}"
        except ValueError:
            return None
    # 纯 25日 / 25号（今日 notion 基准月 8）
    m = re.search(r"(\d{1,2})[日号]", q)
    if m:
        d = int(m.group(1))
        base = _BASE
        # 默认当月 8 月，除非大于当月天数则推月
        try:
            cand = date(2026, 8, d)
            if cand < base - timedelta(days=60):
                cand = date(2026, 9, d)
            elif cand > base + timedelta(days=60):
                cand = date(2026, 7, d)
            # 次日计划统一视作今年
            return cand.strftime("%Y%m%d")
        except ValueError:
            return None
    return None


def parse_time_slot(q: str) -> str | None:
    """sport_time 单日值。先具体日期/月日，再相对词。"""
    v = _parse_date_cn(q)
    if v:
        return v
    v = sport_time(q)
    if v:
        return v
    return None


def parse_weekday(q: str) -> str | None:
    """星期/周X（周六/周日/星期三…）→ 相对基准周一(2026-08-24)那周的日期 yyyyMMdd。

    仅处理本周（不跨周日）；"下周六/下周X" 归下周。
    """
    m = re.search(r"(?:周|星期|礼拜)\s*([日天一二三四五六]|[0-7])", q)
    if not m:
        return None
    ch = m.group(1)
    if ch in "0123456":
        target = int(ch)
    else:
        target = {"日天": 6, "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5}.get(ch)
    if target is None:
        return None
    this = _BASE + timedelta(days=target - _BASE.weekday())
    if "下周" in q:
        this += timedelta(days=7)
    elif "上周" in q:
        this -= timedelta(days=7)
    return _ymd(this)


def parse_month_range(q: str) -> dict | None:
    """X月 → {field:sport_time_range, from, to}（当年 2026）。"""
    m = re.search(r"(\d{1,2})月", q)
    if not m:
        return None
    mon = int(m.group(1))
    if not (1 <= mon <= 12):
        return None
    start = date(2026, mon, 1)
    if mon == 12:
        end = date(2026, 12, 31)
    else:
        end = date(2026, mon + 1, 1) - timedelta(days=1)
    return {"field": "sport_time_range",
            "from": f"{_ymd(start)} 00:00:00", "to": f"{_ymd(end)} 23:59:59"}


def exact(field: str, value: str) -> dict:
    return {"field": field, "value": value}


# 调用方（rules）组装 and 时用 flatten，这里不再重复。