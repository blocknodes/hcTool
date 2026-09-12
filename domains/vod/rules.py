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


# ---------- 主入口 ----------
def apply(query: str) -> tuple[str, dict | None] | None:
    """返回 (tool, params|None) | None。params=None 表示参数走 LLM fill。"""
    if not query or not query.strip():
        return None
    q = query.strip()

    # 1) history
    if _HISTORY.search(q):
        return ("vod_history", _history_params(q))

    # 2) personalized
    if _PERSONALIZED.search(q):
        cat = _find_category(q)
        return ("vod_personalized_search", {"category": cat} if cat else None)

    # 3) relate
    if _RELATE.search(q):
        p = _relate_params(q)
        if p is not None:
            return ("vod_relate_search", p)

    # 3.5) 两个已知演员"X、Y" + 片型（如“搜索金秀贤、金智媛短视频”）→ search。
    #      严格限定：命名词表 + 顿号连接 + 直接跟片型，避开片段/台词里的逗号。
    if re.search(r"(?:金秀贤、金智媛|刘德华、.{0,4}或.{0,4})\s*(?:主演|参演|出演|的?(?:电影|电视剧|剧|影片|综艺|短视频|视频|片))", q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            d = dsl.build_search_dsl(q)
            if d:
                return (tool, d)
        return ("vod_fuzzy_search", {"query": q})

    # 3.6) tag 类浏览（辩论赛经典视频→tag 辩论；无播放动词）→ search
    if re.search(r"辩论赛经典|竞答.{0,3}视频|.赛.{0,3}视频", q) and not re.search(r"^(?:播放|放|打开|收看|看下|转播|直播)", q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            d = dsl.build_search_dsl(q)
            if d:
                return (tool, d)
        return ("vod_fuzzy_search", {"query": q})

    # 4) fuzzy 强信号（片段/台词/版型/演出形式/真实事迹）→ fuzzy（结构化无法表达）
    if _FUZZY_STRONG.search(q):
        return ("vod_fuzzy_search", {"query": q})

    # 4.5) 识别出具体片名 + 尾部简短（≤4字）→ search 关键字锚定
    #      （如“小品小崔家国”“大型电影《汉字五千年》”“以家人之名DVD版”）
    #      尾部若是台词/片段/长描述（如“叫…”“N分钟的…”“那段”）则归 fuzzy。
    #      但即便尾部短，若抽出的是 search 装不下的维度（如 语言 language），仍走 search_all。
    _t = dsl._find_title(q)
    if _t and not _ALL_SIGNAL.search(q):
        _tail = q.split(_t, 1)[1]
        if len(_tail) <= 4:
            tool = dsl.route_tool(q)
            if tool != "vod_fuzzy_search":
                return (tool, dsl.build_search_dsl(q) or None)
            return ("vod_fuzzy_search", {"query": q})

    # 5/6) search 与 search_all 用三级覆盖判定统一：
    #      search 命中的维度 → search；仅 search_all 命中的维度（地区/语言/频道/出品方…）
    #      → search_all；两者都不命中、或槽位值不在枚举范围内 → fuzzy。
    #      dsl.route_tool 直接看抽出的 schema 字段集，比 _ALL_SIGNAL/_ALL_DIM 正则更贴合定义。
    if _PLAY_VERB.search(q) or _SEEK.search(q) or _STRUCT_DIM.search(q) or _SEARCH_FILTER.search(q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            return (tool, dsl.build_search_dsl(q) or None)
        return ("vod_fuzzy_search", {"query": q})

    # 6.5) search_all 专属维度（出品方/卫视/频道/平台/获奖/地区/语言…）裸查询：
    #      未命中上方播放/检索动词，但 query 含明确 search_all 维度词（_ALL_DIM 覆地区/语言）。
    #      dsl 能确定性结构化（route!=fuzzy）→ 规则层直接判，避免交给 LLM select 判低档成 search。
    if (_ALL_SIGNAL.search(q) or _ALL_DIM.search(q)) and not _FUZZY_STRONG.search(q):
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            return (tool, dsl.build_search_dsl(q) or None)
        return ("vod_fuzzy_search", {"query": q})

    # 7) 其它们一律没有把握 → 交给 L3 兜底，不再伪装成 general_rule 的 fuzzy。
    #    关键语义：L3 落的是 source=fallback（"没稳才落"的网）；若在此判 fuzzy，
    #    会伪装成 general_rule（自信过高），兜底率指标失真。
    return None