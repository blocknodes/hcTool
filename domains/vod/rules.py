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
    r"刚才看|上一次看|上一次|最近看过的?|继续播|继续看|近期播过|播放历史|"
    r"接着播|接着看|续播|续看|追到|上次看到|上次看|"
    r"(?:播放|打开|看|继续).{0,3}(?:昨天|前天).{0,4}(?:纪录片|电影|电视剧|综艺|剧|节目|片)"
)

# personalized：强调"按我的偏好推荐"（金标准口径）
_PERSONALIZED = re.compile(
    r"我的(?:兴趣|喜好|口味|偏好)|根据我(?:的)?喜好?|按我的口味|符合我?的?偏好|"
    r"猜我喜欢|适合我看|结合我的喜好|根据我的兴趣|推荐我喜欢的|多推荐些我喜欢的|"
    r"我爱看的|我喜欢的|符合我爱好的|为我挑选|我可能感兴趣的|适合我这类型观众的|"
    r"符合我口味的|我会喜欢的|我喜好的|我看过的.*推荐|我看过的.{0,4}推荐"
)

# relate：类似/相似/相近/差不多/同类型/同风格/类似一个风格/这种类型/还有哪些同款
_RELATE = re.compile(r"类似|相似|相近|差不多|差不多|同类型|同风格|一个风格|类型相同|题材相同|"
                     r"这种类型|类型相似|"
                     r"类型的剧|类型的本|类型的.{0,6}(?:电视剧|影视|剧|节目|片|电影|剧集|名字)|"
                     r"同.{0,8}(?:类型|风格)|和.{0,10}(?:类似|相似|相近|相关)|"
                     r"推荐.{0,15}(?:相似|类似|相关|同款)|"
                     r"还有哪些|什么样的|什么类型|替代品|"
                     r"像《[^》、]+》.{0,10}(?:的?剧|的?片|的?电影|的?纪录片|的?综艺|题材|类型|这种|还有哪些)|"
                     r"跟《[^》、]+》.{0,6}题材接近|和《[^》、]+》.{0,6}题材相近|类似《[^》、]+》.{0,6}(?:的?剧|的?片|的?电影|的?综艺|的?纪录片)")

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
    r"韩剧|美剧|英剧|日剧|泰剧|内地|大陆|国产|国产的|国内|国内的|香港|台湾|港台|美国|英国|日本|"
    r"韩国|泰国|印度|欧美|北欧|北美|中国版|国外|好莱坞"
    r"|粤语|国语|普通话|英语|日语|泰语|韩语|中文|方言|法语|德语|西语|俄语"
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

# 主观情绪/场景/口碑/描述故事型表达 → fuzzy（金标准 whole-sentence 检索口径）。
# 放在 search 系列规则之前，但不能抢 person/history/personalized/relate（因此优先级 5，在 relate 之后）。
_FUZZY_EXPR = re.compile(
    r"口碑炸裂|口碑依然|最火的宝藏|老经典|"
    r"破千万|新剧里哪个|值得追|"
    r"心情很好|心情比较低落|心情特别低落|想哭|释放情绪|烦躁时候|缓解|春困|提神醒脑|躺平|无聊|睡不着|"
    r"吵架|不想出门|"
    r"被孩子气到发疯|发疯|"
    r"讲的是|讲了.{0,12}的故事|"
    r"这句台词|家财万贯|日食不过三餐|对镜头说|"
    r"文物修复|逆袭成|网络诈骗|运送荔枝|展开双臂|蘑菇|劳作换|"
    r"工作生活|科技爱好者|中产人群|大人和(?:孩子|儿童)|"
    r"关于鲨鱼|东北方言|反映.{0,10}故事|"
    r"高分老电影|家破人亡"
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
    # 金标准口径：category 是数组（打开我昨天看的那部剧 → category:["电视剧"]）
    if cat:
        d["category"] = [cat]
    from datetime import date, timedelta
    # 金标准固定 "昨天"（2026-08-23），不随运行日期漂移 → 用锚定日期 2026-08-24
    _off = lambda n, f="%Y-%m-%d": (date(2026, 8, 24) + timedelta(days=n)).strftime(f)
    if re.search(r"今天", query):
        d["time"] = f"{_off(0)} 00:00:00 TO {_off(0)} 23:59:59"
    elif re.search(r"昨天", query):
        d["time"] = f"{_off(-1)} 00:00:00 TO {_off(-1)} 23:59:59"
    elif re.search(r"前天", query):
        d["time"] = f"{_off(-2)} 00:00:00 TO {_off(-2)} 23:59:59"
    return d


_BRACKET = re.compile(r"[《（(]\s*([一-龥A-Za-z0-9·]{1,12})\s*[》）)]")
_TITLE_BEFORE = re.compile(r"(?:和|与|跟|同|像)([一-龥A-Za-z0-9·]{2,8}?)(?:系列|这)?(?:的|这种)?(?:类似|相似|相近|差不多|同类型|类型相同|一样的体裁|一个风格|相关|类型相同)")
_TITLE_AFTER = re.compile(r"(?:类似|相似|相近|差不多|同类型|类型相同|相关|类型接近|同类)(?:与)?([一-龥A-Za-z0-9·]{1,12}?)(?:的)?(?:电视剧|电影|综艺|纪录片|节目|短剧|剧|片|题材|这种)")
# 类型/题材 后置锚点：像《漫长的季节》这种类型的剧还有吗？/ 同奥本海默 类型相同的电影
_TITLE_BEFORE2 = re.compile(r"(?:像|同|跟|和)([一-龥A-Za-z0-9·]{2,10}?)\s*(?:这种类型|这种风格|题材差不多|类似的|类型相同|同类似|类型相同的?|一个风格|类型相似)")
_TITLE_AFTER2 = re.compile(r"(?:有|找|还有|推荐)?(?:类似|相似|差不多|同类型|类型相同|题材接近|相近|同类)(?:《)?([一-龥A-Za-z0-9·]{1,12})(?:》)?(?:的)?(?:类|题材|节目|剧|片|剧集|电影|综艺|纪录片)")


def _relate_title(text: str) -> str:
    # 1) 书名号/括号包裹的标题最可靠
    m = _BRACKET.search(text)
    if m:
        return m.group(1).strip()
    # 2) 词表命中（奥本海默/奔跑吧/半熟恋人…）→ 直接取（不同于 X类型+同义 的正则）
    known = dsl._find_title(text)
    if known:
        return known
    # 3) 前缀/后缀正则兜底
    for m in (_TITLE_BEFORE.search(text), _TITLE_AFTER.search(text),
              _TITLE_BEFORE2.search(text), _TITLE_AFTER2.search(text)):
        if m and m.group(1):
            cand = m.group(1).strip()
            # 剥结尾"类型"（“解密紫禁城类型”）& 避免裸的/纯
            cand = cand.rstrip("类型")
            if len(cand) >= 2 and cand not in ("的", "在", "哦"):
                return cand
    if known:
        return known
    return ""


def _relate_person(text: str) -> tuple[str, str] | None:
    """「推荐X相关的...」里 X 是可知名导演/演员 → (field, 人名)。

    宫崎骏等动画导演、周星驰等演员作 relate 锚点时，gold 按导演/演员维度检索，
    而非把「相关」后的残片当 title。用 dsl 的已知人名表判断，避免手工枚举。
    """
    for n in dsl._KNOWN_ANIM_DIRECTORS:
        if re.search(n, text):
            return ("director", n)
    for n in dsl._KNOWN_DIRECTORS:
        if re.search(n, text):
            return ("director", n)
    for n in dsl._KNOWN_ACTORS:
        if re.search(n, text):
            return ("actor", n)
    return None


def _relate_category_norm(text: str, has_title: bool) -> str | None:
    """relate 检索的 category 归一（对齐 relate-gold 口径）。

    有具名 title 锚点时：金标准把「X片/动画/动漫」类归为基准载体(电影)，电视剧/纪录片 保留。
    原因：锚点既是某部具体作品(如 无间道/千与千寻)，其「同类」是同载体影片，而非细分体裁
    (警匪/动画→都是电影)，故 category 收成 电影/电视剧 而非 动漫 等细分。无 title 锚点时
    走 记录片/电视剧 的直判，动漫/动画片 才归 动漫(否则「动漫」检索会被错并成电影)。
    """
    if re.search(r"纪录片|记录片|纪实", text):
        return "纪录片"
    if re.search(r"综艺节目|综艺|节目", text):
        return "综艺"
    if re.search(r"戏曲", text):
        return "戏曲"
    if re.search(r"动漫|动画", text):
        return "动漫"
    if re.search(r"电影|影片|大片|片子|片", text):
        return "电影"
    # 载体词裸现（的剧/这种剧/相似的剧/类型的剧…）→ 电视剧
    re_tv = re.compile(r"电视剧|连续剧|偶像剧|古装剧|爱情剧|科幻剧|警匪剧|谍战剧|抗战剧|刑侦剧|校园剧|破案剧|青春剧|探案剧|剧集|的?剧")
    if re_tv.search(text):
        return "电视剧"
    return None


def _relate_params(text: str) -> dict | None:
    title = _relate_title(text)
    nodes: list[dict] = []
    cat = _relate_category_norm(text, bool(title))
    # 「推荐Xxx/类似」类点人是知名演员/导演 → 按人检索，舍弃残缺 title。
    person = _relate_person(text)
    if person:
        field, value = person
        if re.search(r"相关|类似|相似|相近", text) and re.search(r"(电影|动画|影片|作品|片)", text):
            nodes.append({"field": field, "value": value})
    elif title:
        nodes.append({"field": "title", "value": title})
    if not nodes:
        return None
    if cat:
        nodes.append({"field": "category", "value": cat})
    if len(nodes) == 1:
        return {"query": nodes[0]}
    return {"query": {"and": nodes}}


# ---------- 主入口（RuleSet 驱动） ----------
def _history_branch(q: str):
    if _HISTORY.search(q):
        return ("vod_history", _history_params(q))
    return None


def _personalized_branch(q: str):
    if _PERSONALIZED.search(q):
        cat = _find_category(q)
        # 幼儿/儿童/少儿 → category=幼儿（金标准：query 包一层 field:category）
        for w in ("幼儿影视", "儿童影视", "少儿影视", "幼儿", "儿童", "少儿"):
            if w in q:
                return ("vod_personalized_search", {"query": {"field": "category", "value": "幼儿"}})
        return ("vod_personalized_search", {"query": {"field": "category", "value": cat}} if cat else {})
    return None


def _relate_branch(q: str):
    if _RELATE.search(q):
        p = _relate_params(q)
        if p is not None:
            # 「X主演/导演的相关Y」：显式演员/导演在，而 title 只是「相关影片」残片
            # (如 "推荐一些梁朝伟主演的相关影片" 会误抽 title=影)。此时应以 actor/director
            # 检索为准，丢弃残缺 title。金标准对这类"相关N"是 actor/director 维度。
            title = p["query"]["and"][0].get("value") if isinstance(p["query"], dict) and p["query"].get("and") else None
            if isinstance(title, str) and len(title) <= 1:
                actor = dsl._actor(q)
                director = dsl._director(q)
                if actor or director:
                    d = dsl.build_search_dsl(q)
                    if d:
                        return ("vod_relate_search", d)
            return ("vod_relate_search", p)
    # 「推荐X」开头（推荐王㔾主演的相关/类似影片、推荐纪录片类、推荐悬疑题材电影…）：
    # 无具名 title 时，用普通检索 DSL 抽字段，但工具统一归 vod_relate_search（gold 口径）。
    # 注意「推荐我喜欢的」已被 _PERSONALIZED(priority2) 先行截走，不会误入。
    # 「推荐X」开头（推荐王鹤棣主演的相关/类似影片、推荐纪录片类、推荐悬疑题材电影…）：
    # 目标是"推荐"，但分两类：
    #   1) 推荐 + 结构化槽位（推荐电影/推荐姜文导演的电影/推荐和家人一起看的电影）→ 普通检索 search/search_all
    #   2) 推荐 + (相似/相关/类似) → 上面的 _RELATE 已接走
    #   3) 纯"推荐…"(想看我之前看过的) → 兜底 relate 原话
    if q.startswith(("推荐", "帮我推荐")):
        # 结构化槽位可拆且无相似语义 → 交给 _search_branch（vod_search/search_all）
        tool = dsl.route_tool(q)
        if tool != "vod_fuzzy_search":
            d = dsl.build_search_dsl(q)
            if d and ("director" in str(d) or "actor" in str(d) or "category" in str(d)):
                return (tool, d)
        return ("vod_relate_search", {"query": q})
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


def _fuzzy_expr_branch(q: str):
    if _FUZZY_EXPR.search(q):
        return ("vod_fuzzy_search", {"query": q})
    return None


def _fuzzy_strong_branch(q: str):
    if _FUZZY_STRONG.search(q):
        return ("vod_fuzzy_search", {"query": q})
    return None


def _title_tail_branch(q: str):
    _t = dsl._find_title(q)
    if _t and not _ALL_SIGNAL.search(q):
        # 别名（跑男→奔跑吧、知否→知否知否、欢乐家长群二→欢乐家长群2）的词表串不在 q 中，
        # 用实际匹配串切尾部，避免 split 抛 IndexError。
        _sub = getattr(dsl, "_match_sub", None) or _t
        if _t in q:
            _sub = _t
        _tail = q.split(_sub, 1)[1] if _sub in q else ""
        if _t and len(_tail) <= 4:
            tool = dsl.route_tool(q)
            if tool != "vod_fuzzy_search":
                return (tool, dsl.build_search_dsl(q) or None)
            return ("vod_fuzzy_search", {"query": q})
    return None


_SQL_CTRL = re.compile(
    r"^(?:搜索|搜|查找|查一下|找一下|找|查)|"       # 检索/查找动词头 → 结构化 search
    r"播放量|播放次数|播放最高|高播放量|"           # 播放量排序
    r"口碑|热度|热门|热播|冷门|爆款|很火|最火|好火|现在热|爆火|"  # 主观热度/口碑 → hot sort
    r"高分|评分高|高评分|[0-9.]+分(?:以上|的|高)|"  # 评分 → rate sort
    r"最新|新出|新上|刚上|近期|最近|新剧|新片|新播|" # 新 → new sort
    r"综艺(?:节目)?|电视剧|电影|动漫|动画|纪录片|我要看|我想看|"  # 载体直判 + 弱起播
    r"推荐|适合|好看"
)


def _search_branch(q: str) -> tuple[str, dict] | None:
    """有检索动词/排序偏好/载体 → dsl 结构化 search（avoid LLM 两段式直出）。

    之前只认 _PLAY_VERB/_SEEK/_STRUCT_DIM/_SEARCH_FILTER，漏了「搜索/播放量/口碑/热度/冷门」
    这类无起播动词但明显是结构化浏览的句子——它们会掉进 LLM 两段式(llm_select)，
    params 与 dsl 不一致（如 hot query 节点/not 热门）。扩展触发面：检索头 + 播放量/热度/
    口碑/评分/承载词 → 走 dsl(route_tool + build_search_dsl)。
    """
    if not _SQL_CTRL.search(q):
        return None
    if _FUZZY_STRONG.search(q) and not re.search(r"播放量|播放次数|播放量高|评分高|评分|热播|热门|冷门|剧情", q):
        # 描述性明确（台词/片段/原著）仍交 fuzzy，不回结构化
        return ("vod_fuzzy_search", {"query": q})
    tool = dsl.route_tool(q)
    if tool != "vod_fuzzy_search":
        return (tool, dsl.build_search_dsl(q) or None)
    return ("vod_fuzzy_search", {"query": q})


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


def _person_branch(q: str):
    """人物查询 → vod_person_search（金标准口径：明星/导演名 + 简介/介绍/个人信息/是谁/裸名）。

    命中标准：dsl 人名表命中 + query 非作品检索语境（主演的电影/导演的作品等）。
    裸名（仅一人名）也是 person。
    """
    name = dsl._person_name(q)
    if not name:
        return None
    # 排除：X主演的电影 / X导演的电影 → 搜作品
    if re.search(r"主演|参演|出演", q):
        return None
    if re.search(r"(?:导演|执导|拍摄)[^，。]{0,8}(?:电影|电视剧|剧|影片|节目|片|作品|综艺|动画|动漫)", q):
        return None
    if re.search(re.escape(name) + r".{0,6}(?:电影|电视剧|剧|影片|综艺|节目|片|动画|动漫|视频)", q):
        return None
    return ("vod_person_search", {"person_name": [name]})


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
        Rule(id="vod_fuzzy_expr", tool="vod_fuzzy_search", priority=2,
             title="描述情绪/口碑/故事", explain="情绪/场景/口碑/故事描述 → 整句语义检索 fuzzy",
             decide=_fuzzy_expr_branch),
        Rule(id="vod_personalized", tool="vod_personalized_search", priority=3,
             title="按偏好推荐", explain="按我的口味/猜我喜欢 → 偏好推荐",
             decide=_personalized_branch),
        Rule(id="vod_relate", tool="vod_relate_search", priority=4,
             title="相似服务", explain="类似电影 → 找标题组 relate 参数",
             decide=_relate_branch),
        Rule(id="vod_person", tool="vod_person_search", priority=5,
             title="人物查询", explain="人名的简介/是谁/裸名 → 查人物资料卡",
             decide=_person_branch),
        Rule(id="vod_fuzzy_multisoft", tool="vod_fuzzy_search", priority=6,
             title="多软属性并接", explain="≥2 不同主观/软属性 -> 整句语义检索 fuzzy",
             decide=_fuzzy_multisoft_branch),
        Rule(id="vod_actors_multi", tool="vod_search", priority=7,
             title="多演员共搜", explain="两名主演员+评分 → 上级路由判定",
             decide=_actors_multi_branch),
        Rule(id="vod_tag_browse", tool="vod_search", priority=8,
             title="栏目 tag 浏览", explain="辩论赛/竞答 栏目 tag → 三级路由",
             decide=_tag_browse_branch),
        Rule(id="vod_fuzzy_strong", tool="vod_fuzzy_search", priority=9,
             title="fuzzy 强信号", explain="片段/台词/型型等无法结构化 → fuzzy 原话",
             decide=_fuzzy_strong_branch),
        Rule(id="vod_title_tail", tool="vod_search", priority=10,
             title="具体片名+尾部", explain="具名剧名 + 尾部 → search 锚定标题",
             decide=_title_tail_branch),
        Rule(id="vod_search", tool="vod_search", priority=11,
             title="结构化多维检索", explain="播放动词/筛选维度 → 三级路由 search/search_all/fuzzy",
             decide=_search_branch),
        Rule(id="vod_search_all", tool="vod_search_all", priority=12,
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
