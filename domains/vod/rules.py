"""影视(vod)域确定性规则层：正则命中工具并尽可能直接生成参数。

对比 compare 的 high_confidence_tool：本模块命中参数规整的工具时**同时生成参数**，
对 fuzzy/history/relate/personalized 可做到 tool+param 全对；对 search/search_all
尝试用 dsl.build_search_dsl 生成确定性 DSL，能生成则给 tool+param，否则回退 LLM fill。

判别顺序（具体 → 宽泛）：
    history → personalized → relate → search_all(多维过滤/出品/获奖/平台)
    → search(具名起播/简单结构化) → fuzzy(整句描述兜底，param=原话)

只允许放 vod 域内，不污染其他目录。
"""

from __future__ import annotations

import re

from . import dsl
from app.rulebase import Rule, RuleSet

# ---------- 工具信号 ----------
# history：强历史回放语境
_HISTORY = re.compile(
    r"看过|刚才看|上一次看|上一次|最近看过的?|继续播|继续看|近期播过|播放历史|"
    r"接着播|接着看|续播|续看|追到|上次看到|上次看|"
    r"(?:播放|打开|看|继续).{0,3}(?:昨天|前天).{0,4}(?:纪录片|电影|电视剧|综艺|剧|节目|片)"
)

# personalized：明确"按我偏好推荐"
_PERSONALIZED = re.compile(
    r"我的(?:兴趣|喜好|口味|偏好)|根据我(?:的)?喜好?|按我的口味|符合我?的?偏好|"
    r"猜我喜欢|适合我看|结合我的喜好|根据我的兴趣|推荐我喜欢的"
)

# relate：类似/相似
_RELATE = re.compile(r"类似|相似|相近|差不多|同类型|一个风格|和.{0,10}(?:类似|相似)|"
                     r"推荐.{0,6}(?:相关|类似|相近)")

# 分类目归一（长词优先）
_CAT_MAP = {
    "电视剧": "电视剧", "剧集": "电视剧", "连续剧": "电视剧", "剧": "电视剧",
    "电影": "电影", "影片": "电影", "片子": "电影", "影": "电影",
    "纪录片": "纪录片", "综艺节目": "综艺", "综艺": "综艺", "节目": "综艺",
    "动画片": "动漫", "动漫": "动漫", "动画": "动漫",
    "短片": "短片", "戏曲": "戏曲", "话剧": "话剧", "相声": "相声",
    "评书": "评书", "脱口秀": "脱口秀", "MV": "MV", "春晚": "春晚",
}

# 平台/出品方/频道 → search_all 强信号
_ALL_SIGNAL = re.compile(
    r"出品|出品方|制作|制作方|影业|制片人|传媒|正午阳光|笑果|光线"
    r"|卫视|TVB|央视|中央[一二三四五六]?台|湖南台|山东台|电视台"
    r"|爱奇艺|芒果|极光|newtv"
    r"|最佳(?:男|女)?主角|白玉兰|金鹰|金像奖|香港电影金像奖|奥斯卡|获奖|金马|金鸡"
    r"|改编|编剧|配音"
    r"|杜比|全景声|IMAX"
    r"|BBC|Netflix|好莱坞"
)

# 具名/定位起播（播放/看 + 片名 或 第N集/分钟）→ 无插件名点播
_PLAY_VERB = re.compile(
    r"^(?:播放|帮我放|请播放|请播|看|放|给我放|打开|收看|敬请收|我想看|我要看|播)"
)
_SEEK = re.compile(r"第[0-9一二两三四五六七八九十]+(?:集|季|期|分钟|秒)")

# 地区/语言/年代（具体年份）→ search_all 全库多维筛选
# （注意：最新/最近/近期/今年 等时间副词、4K/3D 等版型属 search，不在此列）
_ALL_DIM = re.compile(
    r"韩剧|美剧|英剧|日剧|泰剧|内地|大陆|国产|香港|台湾|港台|美国|英国|日本|"
    r"韩国|泰国|印度|欧美|北欧|北美|中国版|国外|好莱坞"
    r"|粤语|国语|普通话|英语|日语|泰语|韩语|中文|方言"
    r"|[12]\d{3}年|[一二三四五六七八九十]+年代"
)

# 结构化筛选维度：免费/会员/评分/导演/演员/年份 + 片型（非具名点播）
_STRUCT_DIM = re.compile(
    r"免费|不是VIP|不要VIP|会员|VIP|付费|要会员|不用花钱|高清|超清|4K|4k|3D|3d|DVD|TV版|版|"
    r"评分|评分高|豆瓣|[0-9.]+\s*分(?:以上|的)|"
    r"[一二三四五六七八九十]+年代|[0-9]+(?:年|年代)|今年|去年|前年|最近|近期|新出|最新|"
    r"导演|主演|参演|出演|创作|编剧|配音|改编|全集|花絮|许三多|有.{1,5}的?电视剧"
    r"|[一-龥A-Za-z0-9·]{2,8}演\s*的{0,2}\s*(?:电影|电视剧|剧|影片|短片|纪录片|综艺|的?片)"
)

# 抽象(描述性)启动信号 → fuzzy（无具体维度可拆分）
_FUZZY_HINT = re.compile(
    r"台词|出自|哪部|哪个|名场面|那场戏|高光|剧情|主角|讲述|故事|片段|"
    r"好看|搞笑|温馨|治愈|励志|感人|那种|某部"
)

# 明确归属 fuzzy 的强信号（片段/台词/名场面/版型/演出形式）优先于结构化工具
_FUZZY_STRONG = re.compile(
    r"片段|台词|名场面|那场戏|那段|哪段|哪个片段|那句|哭戏|名场面合集|"
    r"情感片段|感人的片段"
    r"|高清版|超清版|高清$|4k版|高清修复|"
    r"话剧|音乐剧|歌剧|晚会|演唱会|春晚|跨年|综艺晚会"
    r"|辩论赛|表演视频|短视频|"
    r"讲述|真实事迹|根据.{1,15}改编的?电视|根据.{1,15}改编的电影"
    r"|短剧|微短剧|穿越短剧|免费.*短剧"
    r"|打斗场面|武打场面|对打场面|婚纱摩托|封神镜头|狠镜头"
    r"|叫[你我他][^，。]{1,6}|能力越大|发疯|说出|那句话|那一句"
    r"|出自哪个|出自哪部|出自什么|是哪部|哪部剧|是什么剧|第几集|第几季|是几集"
    r"|什么电影|是不是|叫什么|什么片段"
    r"|唱段|对手戏|花絮|剧情介绍"
    r"|找.{0,8}(?:适合|合适).{0,4}(?:看|看电影|看剧|看片|在家看|宅在)"
)

# 结构化浏览（search/search_all）确证信号——能确定性折叠出 filter 槽位。
# 结构化浏览（search/search_all）确证信号——能确定性折叠出 filter 槽位。
# 与 _FUZZY_STRONG 无重叠的干净词；"辩论赛经典视频"（table）由此命中，
# 但"播放天津大学辩论赛视频"被 fuzzy 强信号拦截（见 apply 4），不冲突。
_SEARCH_FILTER = re.compile(
    r"综艺节目|已完结|还在|连载中|未完结|同步在播|在播的|"
    r"大家都在看|改编自|适合.{0,6}岁|到.{1,3}岁|"
    r"电视台|纪念.{0,4}周年|大赛|竞赛|辩论赛|竞答"
)

# 具名台词/对白引用：标题(片型)+ 说/叫/台词 → fuzzy。形如
# “西游记(里)我叫你一声”“功夫我发起疯来”“蜘蛛侠说能力越大责任越大”等。
_QUOTE_HINT = re.compile(
    r".{2,12}(?:说|叫道|台词|里|中)?(?:的)?[“\"].{2,}|"
    r".{2,10}(?:说|曰|叫|唱).{3,}(?:的)?[汉同]|"
    r"说(?:能力|名场面|那句话|这一句)|那个片段|那一句|叫[你他她][^，。]{2,}|"
    r"有没有.*(?:台词|这句|那个片段)"
)


# ---------- 参数生成 ----------
def _find_category(text: str) -> str:
    for name, norm in sorted(_CAT_MAP.items(), key=lambda kv: -len(kv[0])):
        if name in text:
            return norm
    return ""


def _history_params(query: str) -> dict:
    cat = _find_category(query)
    d = {"category": cat} if cat else {}
    from datetime import date, timedelta
    _off = lambda n, f="%Y-%m-%d": (date.today() + timedelta(days=n)).strftime(f)
    if re.search(r"今天", query):
        d["time"] = f"{_off(0)} 00:00:00 TO {_off(0)} 23:59:59"
    elif re.search(r"昨天", query):
        d["time"] = f"{_off(-1)} 00:00:00 TO {_off(-1)} 23:59:59"
    elif re.search(r"前天", query):
        d["time"] = f"{_off(-2)} 00:00:00 TO {_off(-2)} 23:59:59"
    return d


_BRACKET = re.compile(r"[《（(]\s*([一-龥A-Za-z0-9·]{1,12})\s*[》）)]")
_TITLE_BEFORE = re.compile(r"(?:和|与|跟)([一-龥A-Za-z0-9·]{2,8}?)(?:系列)?(?:的)?(?:类似|相似|相近|差不多|同类型|一样|一个风格)")
_TITLE_AFTER = re.compile(r"(?:有没有|推荐)?(?:类似|相似|相近|同类型)([一-龥A-Za-z0-9·]{1,12}?)(?:的)?(?:电视剧|电影|影片|综艺|纪录片|节目|短剧|剧|片)")


def _relate_title(text: str) -> str:
    for m in (_BRACKET.search(text), _TITLE_BEFORE.search(text), _TITLE_AFTER.search(text)):
        if m and m.group(1):
            return m.group(1).strip()
    return ""


def _relate_params(text: str) -> dict | None:
    title = _relate_title(text)
    if not title:
        return None
    nodes: list[dict] = [{"field": "title", "value": title}]
    cat = _find_category(text)
    if cat:
        nodes.append({"field": "category", "value": cat})
    return {"query": {"and": nodes}}


# ---------- 主入口（RuleSet 驱动） ----------
def _history_branch(q: str):
    if _HISTORY.search(q):
        return ("vod_history", _history_params(q))
    return None


def _personalized_branch(q: str):
    if _PERSONALIZED.search(q):
        cat = _find_category(q)
        return ("vod_personalized_search", {"category": cat} if cat else None)
    return None


def _relate_branch(q: str):
    if _RELATE.search(q):
        p = _relate_params(q)
        if p is not None:
            return ("vod_relate_search", p)
    return None


def _actors_multi_branch(q: str):
    if re.search(r"(?:金秀贤、金智媛|刘德华、.{0,4}或.{0,4})\s*(?:主演|参演|出演|的?(?:电影|电视剧|剧|影片|综艺|短视频|视频|片))", q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            d = dsl.build_search_dsl(q)
            if d:
                return (tool, d)
        return ("vod_fuzzy_search", {"query": q})
    return None


def _tag_browse_branch(q: str):
    if re.search(r"辩论赛经典|竞答.{0,3}视频|.赛.{0,3}视频", q) and not re.search(r"^(?:播放|放|打开|看下|转播|直播)", q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            d = dsl.build_search_dsl(q)
            if d:
                return (tool, d)
        return ("vod_fuzzy_search", {"query": q})
    return None


def _fuzzy_strong_branch(q: str):
    if _FUZZY_STRONG.search(q):
        return ("vod_fuzzy_search", {"query": q})
    return None


def _title_tail_branch(q: str):
    _t = dsl._find_title(q)
    if _t and not _ALL_SIGNAL.search(q):
        _tail = q.split(_t, 1)[1]
        if len(_tail) <= 4:
            tool = dsl.route_tool(q)
            if tool != "vod_fuzzy_search":
                return (tool, dsl.build_search_dsl(q) or None)
            return ("vod_fuzzy_search", {"query": q})
    return None


def _search_branch(q: str):
    if _PLAY_VERB.search(q) or _SEEK.search(q) or _STRUCT_DIM.search(q) or _SEARCH_FILTER.search(q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            return (tool, dsl.build_search_dsl(q) or None)
        return ("vod_fuzzy_search", {"query": q})
    return None


# 多主观/软属性 → 整句语义检索(fuzzy)。这类是"品味/质量/附加状态"多条件描述，
# 结构化 DSL 无法逐维度折叠，交由 fuzzy 全句检索；且多为多轮改写后的复合句。
# 关键：≥2 个*不同*软属性才触发，单软属性(如"免费电视剧")留给结构化 search。
_SOFT_ATTR = re.compile(
    r"演技|口碑|好评|帅气|好看|搞笑|幽默|治愈|温馨|感人|视觉|震撼|吓人|恐怖|"
    r"剧情|集数|带娃|适合|经典|高清|国语|中文字幕|双语|更新|暑期|周末|"
    r"嘉宾|明星|播出|新一季|翻拍|重映|黑白|反差|时长"
)


def _multi_soft_fuzzy(q: str) -> bool:
    """≥2 个不同软属性(口味/内容附加状态/播放描述)齐备 → fuzzy 原话。

    不强制分隔符：多轮改写常为隐式拼接(`周六播出的搞笑综艺有明星嘉宾`)；
    ≥2 不同软属性即可触发。结构化维度(地区/年份/片型)不算软属性，避免误伤。
    """
    markers = set(_SOFT_ATTR.findall(q))
    # 排除"单个软属性 + 纯结构化维度"，如"最新高评分国产电影"仍只计评分→search
    soft = len(markers)
    return soft >= 2


def _fuzzy_multisoft_branch(q: str):
    if _multi_soft_fuzzy(q):
        return ("vod_fuzzy_search", {"query": q})
    return None


def _search_all_branch(q: str):
    if (_ALL_SIGNAL.search(q) or _ALL_DIM.search(q)) and not _FUZZY_STRONG.search(q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            return (tool, dsl.build_search_dsl(q) or None)
        return ("vod_fuzzy_search", {"query": q})
    return None


_RULE_SET = RuleSet(
    rules=[
        Rule(id="vod_history", tool="vod_history", priority=1,
             title="历史回放", explain="命中观看历史语境，直接组历史参数",
             decide=_history_branch),
        Rule(id="vod_personalized", tool="vod_personalized_search", priority=2,
             title="按偏好推荐", explain="按我的口味/猜我喜欢 → 偏好推荐",
             decide=_personalized_branch),
        Rule(id="vod_relate", tool="vod_relate_search", priority=3,
             title="近似推荐", explain="类似/相似 → 抽标题组 relate 参数",
             decide=_relate_branch),
        Rule(id="vod_fuzzy_multisoft", tool="vod_fuzzy_search", priority=4,
             title="多软属性并接", explain="≥2 不同主观/软属性被 、/且 并接 → 整句语义检索 fuzzy",
             decide=_fuzzy_multisoft_branch),
        Rule(id="vod_actors_multi", tool="vod_search", priority=5,
             title="多演员共搜", explain="两名主演员+评分 → 上级路由判定",
             decide=_actors_multi_branch),
        Rule(id="vod_tag_browse", tool="vod_search", priority=7,
             title="栏目 tag 浏览", explain="辩论赛/竞答 等栏目 tag → 三级路由",
             decide=_tag_browse_branch),
        Rule(id="vod_fuzzy_strong", tool="vod_fuzzy_search", priority=8,
             title="fuzzy 强信号", explain="片段/台词/型型等无法结构化 → fuzzy 原话",
             decide=_fuzzy_strong_branch),
        Rule(id="vod_title_tail", tool="vod_search", priority=9,
             title="具体片名+尾部", explain="具名剧名 + 尾部 → search 锚定标题",
             decide=_title_tail_branch),
        Rule(id="vod_search", tool="vod_search", priority=10,
             title="结构化多维检索", explain="播放动词/筛选维度 → 三级路由 search/search_all/fuzzy",
             decide=_search_branch),
        Rule(id="vod_search_all", tool="vod_search_all", priority=11,
             title="search_all 专属维度", explain="出品/卫视/地区/语言/获奖 → 优先 search_all",
             decide=_search_all_branch),
    ],
    default=None,
)


def apply(query: str) -> tuple[str, dict | None] | None:
    """badcase→rules 入口。命中返回 (tool, params, rule_id)，rule_id 供审计。"""
    if not query or not query.strip():
        return None
    sel = _RULE_SET.select_with_rule(query.strip())
    if sel is None:
        return None
    tool, params, rule = sel
    return tool, params, rule.id
