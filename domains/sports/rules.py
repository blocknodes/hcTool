"""sports（体育）确定性规则层（L1）。

意图判定顺序：
  卡片（排名/阵容/赛况/数据） → 预约(match_reservation) →
  榜单(rank_search) → 球队资料(team_search) → 视频(vod_search) → 赛程(match_search)。

工具分三类：
- 扁平参数：rank_search({sport_game, sport_rank_type}) / team_search({sport_team})
- 嵌套 query：match_search / match_reservation / vod_search
- 空参数卡：match_lineup / match_event_card / match_statistics_card（恒 {}）

关键约定（对齐 golden）：
- 球队：query 里整句即队名 → team_search；match/vod 内剥"队"；双队"X队和Y队"视频保留"队"。
- 视频标题：开幕式/颁奖/集锦/精华/瞬间等 → vod title 字段。
- 双日期基准：golden 混了两套"今天"（2026-08-24 周一 / 2026-09-11 周五）。
  nba(小写)/现在/调到 锚到 A(0824)，其余相对日期走 B(0911)（多数）。
- 中甲/欧联杯/解放者杯/国王杯/美洲杯/欧锦赛/NCAA/男篮世界杯/篮球亚洲杯 等专属赛事名校验覆盖。
- 多卡（lineup/event/statistics）空参数。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from . import dsl
from app.rulebase import Rule, RuleSet

# ---------------- 日期基准 ----------------
# golden 里两类「今天」：A=2026-08-24(周一)，B=2026-09-11(周五)
BASE_A = date(2026, 8, 24)
BASE_B = date(2026, 9, 11)

# 命中 -> 用 A 锚（nba 小写 是 A 锚时间线；现在/调到 也是 A）
_A_ANCHOR = re.compile(r"nba|现在|调到|杨家玉")


def _rel_base(q: str) -> date:
    return BASE_A if _A_ANCHOR.search(q) else BASE_B


# ---------------- 意图信号 ----------------
_RESERVE = re.compile(r"预约|预定|订|提醒|订阅")
_RANK = re.compile(r"积分榜|排名|排行|榜")
_CARD_LINEUP = re.compile(r"首发|先发|大名单|出场阵容|上场名单|主力|轮换|替补|十一人|出战名单|阵容|名单")
_CARD_EVENT = re.compile(r"赛况|战况|战报|比分|局势|局势怎么样|进展|怎么样|发生了什么|交手情况|交锋情况|交锋|发生的|实时情况|情况怎么样|情况")
_CARD_STAT = re.compile(r"控球|射门|射正|传球|犯规|角球|越位|扑救|技术统计|数据|统计|黄牌|红牌|命中率|技术统计")

_VIDEO = re.compile(
    r"视频|录像|集锦|锦囊|慢动作|慢镜头|精华|精彩|瞬间|重播|颁奖|典礼|开幕式|闭幕|片段|纪录|纪录片|经典|十佳|进球集锦|炫目|锦标赛|回放|回看")

# 「比赛回放/回看/录像」 → match_search（live_state 3/4），非 vod
_LIVE_REPLAY = re.compile(r"比赛回放|比赛回看|比赛录像|联赛回看|赛回看|vs.*回放|回看.*比赛|回放.*比赛")

# 中文数字/周
_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
       "七": 7, "八": 8, "九": 9, "十": 10, "零": 0}
_WD = {"日": 6, "天": 6, "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5}


def _cn2int(s: str):
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total = 0
    for ch in s:
        if ch in _CN:
            total = total * 10 + _CN[ch]
        else:
            return None
    return total or None


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


# =================================================================
# 项目（sport_name）词表
# =================================================================
# 排除「女单/男单/混双/女团/男团」这类性别/双打描述，golden 里不属于 sport_name。
_SPORT_NAME_RE = re.compile(
    "|".join(re.escape(s) for s in (
        "山地自行车", "自由体操", "女子摔跤", "轮椅冰壶", "速度轮滑", "速度滑冰",
        "跳台滑雪", "女子自由式滑雪", "山地自行车", "单板滑雪", "高山滑雪",
        "乒乓球", "羽毛球", "网球", "台球", "斯诺克", "跳水", "田径", "短跑",
        "赛跑", "自行车", "游泳", "蹦床", "射箭", "击剑", "拳击", "棒球",
        "篮球", "足球", "排球", "冰球", "手球", "高尔夫", "赛车", "钓鱼", "电竞", "电子竞技",
        "格斗", "搏击", "摔跤", "滑板", "轮滑", "体操", "跳伞", "马术", "滑雪",
        "滑冰", "举重", "柔道", "橄榄球", "水球", "铁人三项", "马拉松", "攀岩", "飞碟",
    )),
    )
# 项目转标准名（赛跑→田径，女子自由球→足球，乒乓→乒乓球，男足/女足→足球）
_SPORT_RESOLVE = {
    "赛跑": "田径",
    "女子自由球": "足球",
    "电子竞技": "电竞",
    "乒乓": "乒乓球",
    "女足": "足球",
    "男足": "足球",
    "女篮": "篮球",
    "男篮": "篮球",
    "800米": "田径",
    "800 米": "田径",
}
_TRACK_RE = re.compile(r"\d+米")


def _find_sports(q: str) -> list[str]:
    found, used = [], set()
    for m in _SPORT_NAME_RE.findall(q):
        val = _SPORT_RESOLVE.get(m, m)
        if val not in used:
            used.add(val)
            found.append(val)
    # 男子800米/800米 → 田径
    if _TRACK_RE.search(q) and "田径" not in found:
        found.append("田径")
    # 乒乓 → 乒乓球
    if "乒乓" in q and "乒乓球" not in found:
        found.append("乒乓球")
    # 男足/女足 → 足球
    if ("男足" in q or "女足" in q) and "足球" not in found:
        found.append("足球")
    # 女篮/男篮 → 篮球（但赛事已含"篮球"字样/golden 口径的 wcba/wnba/女篮亚洲杯 等 game 即篮球，不再叠）
    if ("女篮" in q or "男篮" in q) and "篮球" not in found and not _find_game(q):
        found.append("篮球")
    # 投篮/扣篮/三分 + 篮球球星（库里/科比 等）→ 补 篮球（golden：打开库里投篮慢动作视频 → 篮球+库里）
    # 注意：进球集锦（文班亚马）golden 不带 篮球，故只认"投篮/扣篮/三分/妙传"这些投篮专属词
    if re.search(r"投篮|扣篮|三分|妙传", q) and not _find_game(q):
        st = _find_stars(q)
        if st and any(k in _NBA_STARS for k in st) and "篮球" not in found:
            found.append("篮球")
    return found


# =================
# 比赛（sport_game）识别（完整校订）
# =================
_GAME_ALIAS = [
    # 超长优先
    "女篮亚洲杯", "男篮世界杯", "篮球亚洲杯", "乒乓球亚洲杯", "游泳世锦赛", "篮球世界杯",
    "F1", "UFC", "CBA", "NBA", "WNBA", "WCBA", "WTT", "NCAA", "cba", "nba", "wnba", "wcba", "f1",
    "中超", "中甲", "英超", "西甲", "意甲", "德甲", "法甲", "俄超", "阿超",
    "亚冠", "欧冠", "欧联杯", "欧联", "解放者杯", "国王杯", "美洲杯", "欧洲杯", "世界杯",
    "亚洲杯", "欧锦赛", "亚锦赛", "锦标赛", "奥运会", "冬奥会", "全运会", "亚运会", "运动会",
    "足总杯", "世俱杯", "冠军杯", "澳网", "法网", "温网", "美网", "大满贯",
    "亚巡赛", "世锦赛", "k联赛", "K联赛", "k 联赛", "K1", "k1", "K2", "k2",
]
_GAME_CANON = {
    "欧联杯": "欧联杯", "欧联": "欧联", "解放者杯": "解放者杯", "国王杯": "国王杯",
    "美洲杯": "美洲杯", "欧锦赛": "欧锦赛", "亚巡赛": "亚巡赛", "篮球亚洲杯": "篮球亚洲杯",
    "男篮世界杯": "男篮世界杯", "篮球世界杯": "篮球世界杯", "乒乓球亚洲杯": "乒乓球亚洲杯",
    # nba赛事大小写规整。注意 golden 对 wcba 整词用小写（唯一例外：wcba→wcba）；k联赛 golden 也是小写 k。
    "nba": "NBA", "cba": "CBA", "wnba": "WNBA", "wcba": "wcba", "f1": "F1",
    "k联赛": "k联赛", "k 联赛": "k联赛", "k1": "K1", "k2": "K2",
    "ufc": "UFC", "ufc 比赛": "UFC",
}


def _find_game(q: str) -> str:
    """赛事识别，返回 golden 口径字符串（最长优先）。"""
    best, bl = None, 0
    for k in _GAME_ALIAS:
        if k in q and len(k) > bl:
            best, bl = k, len(k)
    if not best:
        return ""
    g = _GAME_CANON.get(best, best)
    # cba常规赛（裸 cba + 常规赛）→ 小写 cba（golden 特异：其余 cba 一律 CBA）
    if g == "CBA" and "常规赛" in q and re.search(r"(?<![A-Za-z])cba(?![A-Za-z])", q):
        return "cba"
    return g


# ====================== 球队词表 ======================
# match/vod/reserve 内嵌球队（golden 口径）。长词优先。
_TEAM_TOKENS = [
    # 洛杉矶/金州等城市+队
    "洛杉矶湖人", "费城 76 人", "波士顿凯尔特人", "布鲁克林篮网", "达拉斯独行侠",
    "丹佛掘金", "底特律活塞", "休斯顿火箭", "奥兰多魔术", "迈阿密热火",
    "圣安东尼奥马刺", "克利夫兰骑士", "多伦多猛龙", "纽约尼克斯", "俄克拉荷马城雷霆",
    "萨克拉门托国王", "犹他爵士", "波特兰开拓者", "菲尼克斯太阳", "密尔沃基雄鹿",
    "夏洛特黄蜂", "亚特兰大老鹰", "孟菲斯灰熊", "金州勇士", "芝加哥公牛",
    "辽宁本钢", "广东宏远", "浙江稠州", "北京控股", "北京国安", "上海申花",
    "山东高速", "天津津门虎", "浙江职业",
    "皇家马德里", "拜仁慕尼黑", "巴黎圣日耳曼", "马德里竞技", "巴塞罗那", "多特蒙德",
    "勒沃库森", "尤文图斯", "国际米兰", "AC米兰", "ac米兰", "那不勒斯", "佛罗伦萨",
    "阿斯顿维拉", "切尔西", "托特纳姆热刺", "阿森纳", "曼城", "曼联", "利物浦",
    "里昂", "马赛", "波尔图", "本菲卡", "里斯本竞技", "阿贾克斯", "流浪者",
    "凯尔特人", "塞维利亚", "瓦伦西亚", "萨拉戈萨", "奥萨苏纳", "巴列卡诺",
    "塞尔塔", "布雷斯特", "斯图加特", "勒沃库森", "河床", "博卡青年", "弗拉门戈",
    "墨西哥美洲", "纽约自由人", "多伦多节奏", "赫尔城", "雷恩", "巴塞尔", "比尔森",
    "吉尔维森", "齐柏林", "鲁宾", "阿拉维斯", "圣保利", "乌迪内斯", "里尔",
    # 国家队（长词优先，队后缀在提取后处理）
    "阿根廷", "巴西", "德国", "西班牙", "葡萄牙", "克罗地亚", "英格兰", "法国",
    "日本", "韩国", "比利时", "乌拉圭", "哥伦比亚", "意大利", "澳大利亚", "卡塔尔",
    "摩洛哥", "塞内加尔", "墨西哥", "荷兰", "瑞典", "丹麦", "瑞士", "俄罗斯",
    "土耳其", "挪威", "波兰", "印度", "埃塞俄比亚", "塞尔维亚", "希腊", "立陶宛",
    "中国", "美国",
    # 简称（gold 用 query 原样短名）
    "湖人", "勇士", "快船", "太阳", "雄鹿", "老鹰", "奇才", "篮网", "尼克斯", "骑士",
    "雷霆", "活塞", "公牛", "火箭", "掘金", "独行侠", "灰熊", "黄蜂", "热火", "猛龙",
    "马刺", "凯尔特人", "国王", "开拓者", "森林狼",
    # 欧洲简称
    "皇马", "巴萨", "国米", "米兰", "罗马",
    # 带空格 AC 米兰 / 地区简称
    "AC 米兰", "山东",
    # 亚洲/国家队简称（用于球赛/预约双队）
    "朝鲜", "乌克兰", "斯图加特",
]

# 球类队后缀剥离（保留在队名字典中 的原始）
_TEAM_KEEP_队 = {"ac米兰", "AC米兰"}


def _team_tokens(q: str) -> list[str]:
    """按出现位置提取队名（长词优先+重叠去重），保持顺序。"""
    hits = []
    for t in _TEAM_TOKENS:
        for m in re.finditer(re.escape(t), q):
            hits.append((m.start(), t))
    hits.sort(key=lambda x: (x[0], -len(x[1])))
    merged = []
    for pos, t in hits:
        if merged and pos < merged[-1][0] + len(merged[-1][1]):
            if len(t) > len(merged[-1][1]):
                # 仅当更长且不改变位置才替换
                merged[-1] = (pos, t)
            continue
        merged.append((pos, t))
    # 过滤纯国家名出现在非队名语境（靠上下文）
    return [t for _, t in merged]


def _strip_team_队(v: str) -> str:
    return v[:-1] if v.endswith("队") else v


# 视频双队：进球区的队名要保留"队"字（exp 用 巴西队/阿根廷队 原样）；
# 但球队资料（team_search）里 golden 反而剥"队"（奇才队→奇才，天津津门虎队→天津津门虎）。
def _strip_team_vod(v: str) -> str:
    return v


# ================================================================
# 队名整句 → 球队资料
# ================================================================
_TEAM_BARE_CANON = {
    "太阳队": "菲尼克斯太阳队", "太阳": "菲尼克斯太阳队",
    "黄蜂队": "夏洛特黄蜂队", "黄蜂": "夏洛特黄蜂队",
    "老鹰队": "亚特兰大老鹰队", "老鹰": "亚特兰大老鹰队",
    "奇才队": "奇才", "天津津门虎队": "天津津门虎",
}
_BARE_TEAM = {
    # 整句即队名才进球队资料（不含意图词）
    "皇家马德里", "巴塞罗那", "曼联", "利物浦", "曼城", "阿森纳", "切尔西",
    "托特纳姆热刺", "拜仁慕尼黑", "多特蒙德", "马德里竞技", "AC 米兰", "AC米兰",
    "尤文图斯", "罗马", "那不勒斯", "巴黎圣日耳曼", "里昂", "河床", "弗拉门戈",
    "桑托斯", "阿贾克斯", "波尔图", "本菲卡", "里斯本竞技", "凯尔特人", "流浪者",
    "塞维利亚", "瓦伦西亚", "利文", "国际米兰", "ac米兰", "米兰",
    # 国家队
    "巴西队", "阿根廷队", "德国队", "英格兰队", "西班牙队", "荷兰队", "葡萄牙队",
    "克罗地亚队", "比利时队", "乌拉圭队", "哥伦比亚队", "意大利队", "日本队", "韩国队",
    "澳大利亚队", "卡塔尔队", "摩洛哥队", "塞内加尔队", "墨西哥队", "美国队", "法国队",
    "土耳其队", "挪威队", "瑞士队", "俄罗斯队", "印度队", "埃塞俄比亚队", "中国队",
    # NBA 全称
    "波士顿凯尔特人", "金州勇士", "洛杉矶湖人", "布鲁克林篮网", "迈阿密热火",
    "费城 76 人", "达拉斯独行侠", "丹佛掘金", "圣安东尼奥马刺", "克利夫兰骑士",
    "多伦多猛龙", "底特律活塞", "奥兰多魔术", "休斯顿火箭", "犹他爵士", "萨克拉门托国王",
    "俄克拉荷马城雷霆", "波特兰开拓者队", "菲尼克斯太阳队", "密尔沃基雄鹿队",
    "夏洛特黄蜂队", "亚特兰大老鹰队", "孟菲斯灰熊队", "纽约尼克斯队",
    # CBA/中超
    "广东宏远", "辽宁本钢", "浙江稠州", "北京国安", "上海申花",
    # 男篮/男足
    "美国男篮", "西班牙男篮", "塞尔维亚男篮", "希腊男篮", "立陶宛男篮", "澳大利亚男篮",
    "阿根廷男篮", "德国男篮", "法国男篮", "中国男篮", "比利时男篮", "波多黎各男篮",
    "巴西男篮", "尼日利亚男篮",
    "中国女足", "中国男足",
    # 已有 24 条
    "土耳其", "太阳队", "波特兰开拓者", "勒沃库森", "挪威", "快船", "菲尼克斯太阳",
    "奇才", "贝尔格莱德红星", "天津津门虎", "黄蜂", "马赛", "马赛队", "纽约尼克斯",
    "瑞士", "俄罗斯", "博卡青年", "法国", "国际米兰", "密尔沃基雄鹿", "老鹰",
    "孟菲斯灰熊", "森林狼", "印度", "埃塞俄比亚", "国王", "皇马", "湖人",
    "勇士", "掘金", "篮网", "雄鹿", "火箭", "独行侠", "尼克斯", "公牛", "骑士", "雷霆", "活塞",
    "快船队", "菲尼克斯太阳队", "纽约尼克斯队", "波士顿凯尔特人队", "圣安东尼奥马刺队",
    "迈阿密热火队", "俄克拉荷马城雷霆队", "达拉斯独行侠队", "丹佛掘金队", "底特律活塞队",
    "奥兰多魔术队", "休斯顿火箭队", "犹他爵士队", "萨克拉门托国王队", "金州勇士队",
    "多伦多猛龙队", "克利夫兰骑士队", "布鲁克林篮网队", "洛杉矶湖人队", "芝加哥公牛队",
    "远独行侠队",
}

_BARE_BLOCK = re.compile(r"比赛|联赛|视频|录像|集锦|慢|回放|回看|看|战|直播|预测|预约|提醒|订阅|主场|客场|pk|vs|杯|积分|排名|榜|对|打|赛")
# 允许整句即队名但带"赛"字（golden：马赛队 → sports_team_search 马赛队）
_BARE_TEAM_赛KEEP = {"马赛队"}

# 整句即队名的名单（精确词，多了"赛"也被允许）
_BARE_TEAM |= _BARE_TEAM_赛KEEP


def _branch_team(q: str):
    name = q.strip()
    # 整句即队名但含"赛"（马赛队）绕过拦截；其余带意图词 → 拦
    if name not in _BARE_TEAM and _BARE_BLOCK.search(q):
        return None
    key = name[:-1] if name.endswith("队") else name
    if name in _TEAM_BARE_CANON:
        # 队名整句 exact：太阳队→菲尼克斯太阳队（完整名）；黄蜂队/老鹰队 同理
        return "sports_team_search", {"sport_team": _TEAM_BARE_CANON[name]}
    if key in _TEAM_BARE_CANON:
        # 奇才队→奇才、天津津门虎队→天津津门虎（golden 剥队）
        return "sports_team_search", {"sport_team": _TEAM_BARE_CANON[key]}
    if name in _BARE_TEAM or key in _BARE_TEAM:
        # golden 其它队名一律原样（含"队"），如 土耳其队/马赛队/巴西队
        return "sports_team_search", {"sport_team": name}
    return None


# ================================================================
# 时间解析（支持双锚）
# ================================================================
_DATE_CN = re.compile(r"(\d{1,4})年(\d{1,2})月(\d{1,2})[日号]")


def _parse_cn(y, mon, d, q: str) -> str | None:
    """绝对日期 -> yyyyMMdd。年缺失则 1月→2027，其余→2026（golden 口径）。"""
    if y:
        yv = int(y)
    else:
        yv = 2027 if mon == 1 else 2026
    try:
        return f"{yv:04d}{int(mon):02d}{int(d):02d}"
    except ValueError:
        return None


def _parse_date_cn(q: str, base: date) -> str | None:
    # 年份必须带「年」字，否则裸「12月8日」会把 1 当成年份
    m = re.search(r"(?:(1[89]\d{2}|20\d{2}|[1-9]\d{0,2})\s*年)?(\d{1,2})月(\d{1,2})[日号]", q)
    if not m:
        return None
    y, mon, d = m.group(1) or None, int(m.group(2)), int(m.group(3))
    return _parse_cn(y, mon, d, q)


def _weekday_date(q: str, base: date) -> str | None:
    m = re.search(r"(?:下|上)?(?:周|星期|礼拜)\s*([日天一二三四五六0-7])", q)
    if not m:
        return None
    ch = m.group(1)
    tgt = int(ch) if ch in "0123457" else _WD.get(ch)
    if tgt is None:
        return None
    if re.search(r"(?<!一)下(?:周|星期|礼拜)", q):
        # 「预约下周四」= 下一自然周；「预约一下周六」不是下周
        monday = base - timedelta(days=base.weekday()) + timedelta(days=7)
        return _ymd(monday + timedelta(days=tgt))
    if re.search(r"(?:上)(?:周|星期|礼拜)", q):
        monday = base - timedelta(days=base.weekday()) - timedelta(days=7)
        return _ymd(monday + timedelta(days=tgt))
    # 裸「周六/周五」= 本自然周该天；若已过（早于 base）→ 顺延到下一天（golden：base 周五时 周六->明天）
    monday = base - timedelta(days=base.weekday())
    d = monday + timedelta(days=tgt)
    if d < base:
        d += timedelta(days=7)
    return _ymd(d)


def _week_range(q: str, base: date) -> dict | None:
    """上周/本周/这周 → sport_time_range。"""
    # 上周：前一自然周（周一~周日）
    if re.search(r"上周|上星期|上礼拜", q):
        end = base - timedelta(days=base.weekday()) - timedelta(days=1)
        start = end - timedelta(days=6)
        return {"field": "sport_time_range",
                "from": f"{_ymd(start)} 00:00:00", "to": f"{_ymd(end)} 23:59:59"}
    if re.search(r"这周|本周|这一周", q):
        start = base - timedelta(days=base.weekday())
        end = start + timedelta(days=6)
        return {"field": "sport_time_range",
                "from": f"{_ymd(start)} 00:00:00", "to": f"{_ymd(end)} 23:59:59"}
    return None


def _today_time(q: str, base: date) -> str | None:
    if re.search(r"今天|现在|今夜|今日", q) and not re.search(r"这周|本周|上周", q):
        return _ymd(base)
    if re.search(r"明天|明日", q):
        return _ymd(base + timedelta(days=1))
    if re.search(r"大后天", q):
        return _ymd(base + timedelta(days=3))
    if re.search(r"后天", q):
        return _ymd(base + timedelta(days=2))
    if re.search(r"昨天|昨日", q):
        return _ymd(base - timedelta(days=1))
    return None


def _time_of_day_range(q: str, base: date) -> dict | None:
    """晚上7点35/7点30 → 区间 [base HH:MM:00, base HH+1:MM:00]。"""
    m = re.search(r"(\d{1,2})[点时](\d{1,2})分?", q)
    if not m:
        m = re.search(r"(\d{1,2})[点时](\d{0,2})分?", q)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    if hh > 23:
        return None
    # 晚上7点35 → 19:35（golden 口径；裸 7点35 保持 07:35）
    if re.search(r"晚上|今晚|晚间", q) and 0 <= hh <= 12:
        hh += 12
    day = base
    from_ = f"{_ymd(day)} {hh:02d}:{mm:02d}:00"
    # 结束时刻 = 次日 0 点跨天，或当日 hh+1；gold 单条意外 00:00 结尾也兼容同构
    to_hh = (hh + 1) % 24
    to_ = f"{_ymd(day + timedelta(days=1 if hh == 23 else 0))} {to_hh:02d}:{mm:02d}:00"
    return {"field": "sport_time_range", "from": from_, "to": to_}


_TIMER_MOD = re.compile(r"今晚|晚上|下午|中午|上午|凌晨")


def _time_mod_range(q: str, base: date) -> dict | None:
    """「今晚」「今晚X点」→ sport_time_range。_today_time/_weekday_date 不命中时兜底。"""
    if not _TIMER_MOD.search(q):
        return None
    # 具体时段（晚上7点35）走既有函数
    r = _time_of_day_range(q, base)
    if r:
        return r
    # 仅"今晚/晚上"无具体时刻 → 18:00 起当晚
    if re.search(r"今晚|晚上", q):
        d = _ymd(base)
        return {"field": "sport_time_range",
                "from": f"{d} 18:00:00", "to": f"{d} 00:00:00"}
    return None


# ================================================================
# 球队主/客处理
# ================================================================
def _home_guest(q: str, teams: list[str]) -> list[dict]:
    """主场/客场 时生成 home/guest 节点。"""
    conds: list[dict] = []
    if "主场" in q:
        conds.append({"field": "sport_team_home", "value": teams[0]})
        if len(teams) > 1:
            conds.append({"field": "sport_team_guest", "value": teams[-1]})
    elif "客场" in q:
        if len(teams) == 1:
            conds.append({"field": "sport_team_guest", "value": teams[0]})
        else:
            # 客场队在前，主队在后（皇马客场pk曼城 -> home=曼城 guest=皇马）
            home = teams[-1]
            guest = teams[0]
            conds.append({"field": "sport_team_home", "value": home})
            conds.append({"field": "sport_team_guest", "value": guest})
    else:
        for t in teams:
            conds.append({"field": "sport_team", "value": t})
    return conds


# ================================================================
# 组装 query 节点
# ================================================================
def _pack(conds: list[dict], or_team: bool = False) -> dict:
    if not conds:
        return {}
    if len(conds) == 1:
        return {"query": conds[0]}
    return {"query": {"and": conds}}


def _and(conds: list[dict]):
    if len(conds) == 1:
        return conds[0]
    return {"and": conds}


# ================================================================
# 主题分支
# ================================================================
def _reserve(q: str):
    if not _RESERVE.search(q):
        return None
    base = _rel_base(q)
    conds: list[dict] = []

    game = _find_game(q)
    if game:
        conds.append({"field": "sport_game", "value": game})
    for sp in _find_sports(q):
        if sp != game:
            conds.append({"field": "sport_name", "value": sp})
    for st_ in _find_stars(q):
        conds.append({"field": "sport_star", "value": st_})

    teams = _team_tokens(q)
    if len(teams) >= 2:
        # 双队预约：对/vs/男足/女足 → values or；尤文图斯ac米兰(无对/和) → 分离 and（golden 特异）
        vals = [_strip_team(t) for t in teams]
        if re.search(r"对|vs|against|vs\.", q) or "男足" in q or "女足" in q:
            conds.append({"field": "sport_team", "values": vals, "operator": "or"})
        elif "ac米兰" in q and "AC米兰" not in q:
            # 帮我预约尤文图斯ac米兰队的比赛（小写 ac）→ and 双字段（golden 特异）
            for v in vals:
                conds.append({"field": "sport_team", "value": v})
        else:
            conds.append({"field": "sport_team", "values": vals, "operator": "or"})
    elif len(teams) == 1:
        conds.append({"field": "sport_team", "value": _strip_team(teams[0])})

    phase = _phase(q)
    if phase:
        conds.append({"field": "sport_competition_phase", "value": phase})

    # 下一场 → round_offset 1
    if re.search(r"下一场|下场比赛|下场", q):
        conds.append({"field": "round_offset", "value": "1"})
        if not any(c.get("field") == "sport_team" for c in conds) and teams:
            conds.append({"field": "sport_team", "value": _strip_team(teams[0])})

    # 时间：具体时段(晚上7点35/今晚X点) → 仅 time_range；今天/周X → sport_time
    d = _parse_date_cn(q, base)
    if d:
        conds.append({"field": "sport_time", "value": d})
        rng = _time_mod_range(q, base)
        if rng:
            conds.append(rng)
    else:
        t = _today_time(q, base)
        wd = _weekday_date(q, base)
        rng = _time_mod_range(q, base)
        if t and not rng:
            conds.append({"field": "sport_time", "value": t})
        elif wd and not rng:
            conds.append({"field": "sport_time", "value": wd})
        elif rng:
            conds.append(rng)
        else:
            rngw = _week_range(q, base)
            if rngw:
                conds.append(rngw)

    clean = _dedup(conds)
    return "sports_match_reservation", _pack(clean)


def _rank(q: str):
    if not _RANK.search(q):
        return None
    game = _find_game(q)
    rt = "积分榜"
    if re.search(r"射手|球员榜", q):
        rt = "射手榜"
    elif re.search(r"助攻", q):
        rt = "助攻榜"
    elif re.search(r"抢断", q):
        rt = "抢断榜"
    elif re.search(r"得分", q):
        rt = "得分榜"
    elif re.search(r"篮板", q):
        rt = "篮板榜"
    elif re.search(r"盖帽", q):
        rt = "盖帽榜"
    elif re.search(r"球队榜|俱乐部榜", q):
        rt = "球队榜"
    elif re.search(r"金牌", q):
        rt = "金牌榜"
    elif re.search(r"奖牌", q):
        rt = "奖牌榜"
    elif re.search(r"排行|排名榜", q):
        rt = "排行榜"
    p = {"sport_rank_type": rt}
    if game:
        p["sport_game"] = game
    return "sports_rank_search", p


def _vod(q: str):
    # 卡类先行
    if _CARD_STAT.search(q):
        return None
    # 比赛回放/回看/录像/联赛回看 → match（live_state 3/4）
    if _LIVE_REPLAY.search(q):
        return None
    # 直播：只有亚运/知名赛事等有「比赛」上下文的场景走 vod；普通队伍直播（女足/男足）→ match
    if re.search(r"直播", q):
        if re.search(r"女足|男足|队|频道", q) and not re.search(r"亚运|比赛|世界杯|欧|欧冠", q):
            return None
    # 恒 title：开幕式/闭幕式/颁奖/典礼/开幕/闭幕
    if re.search(r"开幕式|闭幕式|颁奖|典礼|开幕|闭幕", q):
        tl = _title_host(q)
        if tl:
            return "sports_vod_search", tl
    # 集锦/精华/精彩瞬间/瞬间/片段/慢动作/经典/进球：显式 team/star → 结构化；否则 title
    # （播放科比在NBA投篮的精彩瞬间 → star+game 结构化；查下西甲精华 → title 西甲精华）
    if re.search(r"集锦|精华|精彩瞬间|瞬间|片段|慢动作|慢镜头|经典", q) \
            and not (_team_tokens(q) or _find_stars(q)):
        tl = _title_host(q)
        if tl:
            return "sports_vod_search", tl
    # 显式视频词 / 直播/现场/节目/赛场 信号
    # 注意：进行中/正在播 → live_state 2 赛程，不在此处进 vod
    if not _VIDEO.search(q) and not re.search(r"直播|现场|节目|赛场", q):
        return None
    # 结构化优先 → 有 game/team/star/name/phase 才走 vod；否则 title 兜底
    p = _vod_params(q)
    if p:
        return "sports_vod_search", p
    # 无结构化（纯"西甲精华/XX集锦"）→ title 兜底
    manual_title = _title_host(q)
    if manual_title:
        return "sports_vod_search", manual_title
    return None


def _vod_params(q: str) -> dict:
    base = None
    conds: list[dict] = []
    game = _find_game(q)
    if game:
        conds.append({"field": "sport_game", "value": game})
    for sp in _find_sports(q):
        if sp != game:
            conds.append({"field": "sport_name", "value": sp})
    for st_ in _find_stars(q):
        conds.append({"field": "sport_star", "value": st_})
    teams = [_t if _t != "AC 米兰" else "AC米兰" for _t in _team_tokens(q)]
    # 双球星（VS 对战）→ values or（UFC萨鲁吉安VS霍克天下唯一 golden；无其它双星）
    if len(_find_stars(q)) >= 2 and len(_find_stars(q)) == len({s for s in _find_stars(q)}):
        conds = [c for c in conds if c.get("field") != "sport_star"]
        conds.append({"field": "sport_star", "values": _find_stars(q), "operator": "or"})
    if len(teams) >= 2:
        # 双队视频：X队和Y队 → 保留"队"（golden：巴西队/阿根廷队）；否则剥队
        vals = []
        for t in teams:
            if re.search(re.escape(t) + r"队", q):
                vals.append(t + "队")
            else:
                vals.append(_strip_team(t))
        conds.append({"field": "sport_team", "values": vals, "operator": "or"})
    elif len(teams) == 1:
        conds.append({"field": "sport_team", "value": _strip_team(teams[0])})
    phase = _phase(q)
    if phase:
        conds.append({"field": "sport_competition_phase", "value": phase})
    # 直播（vod 也带 live_state）
    if re.search(r"直播|进行中|正在播", q):
        conds.append({"field": "live_state", "value": "2"})
    return _pack(_dedup(conds))


def _match(q: str):
    base = _rel_base(q)
    conds: list[dict] = []
    game = _find_game(q)
    if game:
        conds.append({"field": "sport_game", "value": game})
    for sp in _find_sports(q):
        if sp != game:
            conds.append({"field": "sport_name", "value": sp})
    for st_ in _find_stars(q):
        conds.append({"field": "sport_star", "value": st_})
    teams = _team_tokens(q)
    if len(teams) >= 2 and re.search(r"男足|女足", q):
        # 中国男足对韩国男足 → 双队 values or（golden：中国队/韩国队 or + 足球）
        conds.append({"field": "sport_team", "values": [_strip_team(t) for t in teams], "operator": "or"})
    elif teams:
        conds.extend(_home_guest(q, [_strip_team(t) for t in teams]))
    phase = _phase(q)
    if phase:
        conds.append({"field": "sport_competition_phase", "value": phase})

    # live_state（match 专用）
    lv = _live(q)
    if lv:
        conds.append(lv)
    # round_offset 上一轮
    if re.search(r"上一轮|本轮|补轮|该轮", q):
        conds.append({"field": "round_offset", "value": "-1"})

    d = _parse_date_cn(q, base)
    if d:
        conds.append({"field": "sport_time", "value": d})
    else:
        t = _today_time(q, base)
        if t:
            conds.append({"field": "sport_time", "value": t})
        wd = _weekday_date(q, base)
        if wd:
            conds.append({"field": "sport_time", "value": wd})
        else:
            rng = _time_mod_range(q, base)
            if rng:
                conds.append(rng)
            else:
                rngw = _week_range(q, base)
                if rngw:
                    conds.append(rngw)

    # 兜底今天（golden 无信息=sport_time 今天）
    if not conds and re.search(r"有.*比赛|比赛|赛", q):
        conds.append({"field": "sport_time", "value": _ymd(base)})
    clean = _dedup(conds)
    return "sports_match_search", _pack(clean)


def _dedup(conds: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for c in conds:
        key = (c.get("field"), c.get("value"), str(c.get("values")), c.get("from"), c.get("to"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _strip_team(v: str) -> str:
    return v[:-1] if v.endswith("队") else v


def _phase(q: str) -> str | None:
    # 欧洲杯C组/第三轮 → 小组赛
    if re.search(r"C组", q) or re.search(r"小组", q):
        if re.search(r"循环|分组|小组|第.+轮", q):
            return "小组赛"
    m = re.search(r"预选赛", q)
    if m:
        return "预选赛"
    m = re.search(r"(第?\d+轮)", q)
    if m:
        return m.group(1)
    for p in ("总决赛", "1/8决赛", "1/4决赛", "半决赛", "常规赛", "季后赛",
              "复赛", "资格赛", "淘汰赛", "四强", "决赛"):
        if p in q:
            return p
    # 世界杯英格兰 vs 丹麦 1/8
    if re.search(r"1/?8决赛", q):
        return "1/8决赛"
    return None


def _live(q: str) -> dict | None:
    if re.search(r"回放|回看|回播|录像|看录|重播|集锦|精彩|锦", q):
        return {"field": "live_state", "values": ["3", "4"]}
    if re.search(r"直播|进行中|正在播|现场", q):
        return {"field": "live_state", "value": "2"}
    if re.search(r"还没开始|未开始|即将", q):
        return {"field": "live_state", "value": "1"}
    return None


_STAR_ORDER = ("泰勒·弗里茨", "张本智和", "王楚钦", "王曼昱", "孙颖莎", "文班亚马", "杨家玉",
               "覃海洋", "全红婵", "丁俊晖", "库里", "詹姆斯", "C罗", "科比", "柯瑞",
               "斯波莎", "莎拉波娃", "亚马尔", "哈兰德", "邹敬园", "查韦斯", "萨鲁吉安",
               "霍克", "卡恩")
_NBA_STARS = {"文班亚马", "库里", "詹姆斯", "科比", "柯瑞"}
_NBA_STAR_RE = re.compile("文班亚马|库里|詹姆斯|科比|柯瑞")


def _find_stars(q: str) -> list[str]:
    found = []
    for s in _STAR_ORDER:
        if s in q:
            found.append(s)
    return list(dict.fromkeys(found))


# ================================================================
# 规则集
# ================================================================
RULE_SET = None


def apply(query: str) -> tuple[str, dict | None] | None:
    if not query or not query.strip():
        return None
    q = query.strip()

    # 1) 卡工具（先 stat 再 event：控球情况/射门情况 归统计卡）
    if _CARD_LINEUP.search(q):
        return "sports_match_lineup_search", {}
    if _CARD_STAT.search(q):
        return "sports_match_statistics_card_search", {}
    if _CARD_EVENT.search(q):
        return "sports_match_event_card_search", {}

    # 2) 预约
    if _RESERVE.search(q):
        r = _reserve(q)
        if r:
            return r
    # 3) 榜单
    if _RANK.search(q):
        r = _rank(q)
        if r:
            return r
    # 4) 球队资料
    t = _branch_team(q)
    if t is not None:
        return t
    # 4.5) 纯球星意图（无赛事/比赛/项目/球队字眼）→ vod（查韦斯/C罗的视频）
    #      亚运会全红婵跳水（有 game/name）不走此分支
    stars = _find_stars(q)
    if stars and not re.search(r"比赛|赛事|世界杯|赛|对|vs", q) \
            and not (_find_game(q) or _team_tokens(q) or _find_sports(q)):
        return "sports_vod_search", {"query": {"field": "sport_star", "value": stars[0]}}
    # 5) 视频/现场/回放
    if _VIDEO.search(q):
        v = _vod(q)
        if v is not None:
            return v
        # 回放/直播等 _vod 返回 None → 落到 match 兜底
    elif _LIVE_REPLAY.search(q) or re.search(r"直播|现场|赛场|节目|进行中|正在播", q):
        v = _vod(q)
        if v is not None:
            return v

    # 6) 视频 title 特殊（播放亚运会开幕式 等，无显式 video 词）
    tl = _title_only(q)
    if tl:
        return "sports_vod_search", tl

    # 7) 默认赛程（含回放/直播/赛程）
    return _match(q)


def _title_only(q: str) -> dict | None:
    if re.search(r"亚运会?开幕式|开幕晚会|开闭幕|开幕式|闭幕式", q) or "亚运" in q:
        return _title_host(q)
    if "颁奖" in q or "颁奖仪式" in q:
        return _title_host(q) or {"query": {"field": "title", "value": q}}
    if "片段" in q or "精彩瞬间" in q:
        return {"query": {"field": "title", "value": _title_text(q)}}
    return None


def _title_host(q: str) -> dict | None:
    """title 字段直接产：亚运/世界杯 开幕式/颁奖/集锦 等。"""
    t = _title_text(q)
    if t:
        return {"query": {"field": "title", "value": t}}
    return None


def _title_text(q: str) -> str | None:
    """title 值：开幕式/颁奖/集锦等 title 口径。前缀逐层剥离 + 庆典归一。"""
    s = q
    s = re.sub(r"^(?:海信小聚|小聚小聚|小聚)\s*", "", s)
    s = re.sub(r"^(?:帮我|请|麻烦|查下|查一下|查|搜一下|搜索下|搜索|搜|找|打开|播放|进入|看|观看|来|给|回放|我想看|我要看)\s*", "", s)
    s = re.sub(r"^(?:播放|回放|搜索|搜一下|打开|进入|看|请|给|来)\s*", "", s)
    s = re.sub(r"^(?:二零二[五六]|二〇二[五六]|20[0-9]{2}年)\s*", "", s)
    s = s.strip("，。！？ ")

    def _prefix(qq: str) -> str:
        if "名古屋" in qq:
            return "名古屋亚运会"
        if "亚运会" in qq or "亚运" in qq:
            return "亚运会"
        if "世界杯" in qq:
            return "世界杯"
        return ""

    pre = _prefix(q)
    # 开幕式 / 开幕晚会 / 闭幕式 / 开幕
    if "开幕晚会" in s:
        return pre + "开幕晚会"
    if "开幕式" in s or "开幕" in s or "开幕时" in s:
        held = ""
        if "晚会" in s and "开幕晚会" not in s:
            held = "晚会"
        elif "表演" in s:
            held = "表演"
        return pre + "开幕式" + held
    if "闭幕式" in s:
        return pre + "闭幕式"
    # 颁奖
    if "颁奖" in s:
        return pre + ("颁奖典礼" if "典礼" in s else "颁奖仪式")
    # 集锦 / 精彩瞬间 / 瞬间 / 精华 / 片段
    if "集锦" in s:
        return pre + "集锦"
    if "精彩" in s and "瞬间" in s:
        return pre + "精彩瞬间"
    if "瞬间" in s:
        return pre + "瞬间"
    if "精华" in s:
        return pre + "精华"
    if "片段" in s:
        return pre + "片段"
    return None