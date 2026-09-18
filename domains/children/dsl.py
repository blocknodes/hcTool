"""少儿(children)确定性 DSL 生成器：从 query 抽确定性槽位，组装 educ_search/search_all。

结构与 vod dsl 同构：字段/取值对齐 schema（ExactFieldCondition）。
build_search_dsl(query) -> (params | None)，无法精确合成返回 None → fuzzy。
build_query_dsl(query) -> (query_node | None)：仅合成 query 子节点（供 relate 复用）。

字段集（educ_search 可表达）：
  title, content_type, children_second_genre, children_third_genre, training_objectives,
  role, is_fee, age_range, language, gender, festival, company, multiple_intelligences
educ_search_all 额外：country(国家), release_time(年份)
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# ============================================================
# 槽位词表（golden 实证归一）
# ============================================================

_ADULT_ANIM = re.compile(r"成人动画片")

_CONTENT_MAP = [
    ("动画片儿", "动画"), ("动画片", "动画"), ("卡通片", "卡通"), ("动画剧", "动画剧"),
    ("卡通动画", "卡通"), ("卡通", "卡通"), ("动画", "动画"),
]

_SECOND_MAP = {
    "动漫": "动漫", "漫画": "漫画", "绘本": "绘本", "儿歌": "儿歌", "玩具": "玩具",
    "真人": "真人", "音频": "音频", "宠物": "宠物", "早教": "早教", "科普": "科普",
    "英语": "英语", "国学": "国学", "数学": "数学", "才艺": "才艺", "故事": "故事",
    "家长课堂": "家长课堂", "经典": "经典", "国学启蒙": "国学启蒙", "数学启蒙": "数学启蒙",
    "动画电影": "动画电影", "音乐": "音乐", "电影": "电影",
}

_THIRD_MAP = {
    "搞笑": "搞笑", "工程车": "工程车", "红色旋律": "红色旋律",
    "交通工具": "交通工具", "竞技": "竞技", "机甲": "机甲", "科幻": "科幻",
    "历史": "历史", "礼仪品德": "礼仪品德", "励志": "励志", "美食": "美食",
    "魔幻": "魔幻", "情绪管理": "情绪管理", "亲子": "亲子", "奇幻": "奇幻",
    "热血": "热血", "生活日常": "生活日常", "推理": "推理", "悬疑": "悬疑",
    "温馨治愈": "温馨治愈", "性格培养": "性格培养", "习惯养成": "习惯养成",
    "益智动画": "益智动画", "友情": "友情", "动作": "动作", "感动": "感动",
    "冒险": "冒险", "校园": "校园", "古诗": "古诗", "儿歌": "儿歌",
    "分级阅读": "分级阅读", "益智": "益智", "安全知识": "安全知识",
    "动物百科": "动物百科", "恐龙": "恐龙", "自然": "自然",
    "动物": "动物", "礼貌": "礼貌", "专注力": "专注力", "自律": "自律",
    "舞": "舞", "汉字": "汉字",
}

_OBJECTIVE_MAP = {
    "情绪管理": "情绪管理", "英语启蒙": "英语启蒙", "国学启蒙": "国学启蒙",
    "认知启蒙": "认知启蒙", "数学启蒙": "数学启蒙", "逻辑思维": "逻辑思维",
    "专注力": "专注力", "社交": "社交", "语言表达": "语言表达",
    "习惯养成": "习惯养成", "性格培养": "性格培养", "安全意识": "安全意识",
}

_LANG_MAP = [
    ("中文版", "国语版"), ("国语版", "国语版"),
    ("普通话版", "国语版"), ("普通话", "普通话"),
    ("英语版", "英文版"), ("英文版", "英文版"), ("英文", "英文"), ("英语", "英语"),
    ("日文版", "日语版"), ("日语版", "日语版"), ("日语", "日语版"), ("日文", "日语版"),
    ("中文", "中文"), ("国语", "国语版"),
    ("韩语", "韩语"), ("法语", "法语"),
]

_COUNTRY_MAP = [
    ("俄罗斯", "俄罗斯"), ("加拿大", "加拿大"), ("澳大利亚", "澳大利亚"),
    ("意大利", "意大利"), ("国产", "中国"), ("欧美", "欧美"), ("中国", "中国"),
    ("日本", "日本"), ("美国", "美国"), ("英国", "英国"), ("韩国", "韩国"),
    ("法国", "法国"), ("德国", "德国"),
]

# 具名标题/角色名 词表（长优先）——golden 穷举。绝大多数 title 在 query 字面出现。
_TITLES = (
    '宝宝巴士奇妙学古诗', '汽车世界之颜色乐园', '超级宝贝jojo', '我是不白吃日常篇',
    '我的磁力拼搭世界', '猪猪侠之超星萌宠', '宝宝巴士亲子互动', '迷你特工队全集',
    '哪吒之魔童闹海', '小砾与工程家族', '喜羊羊与灰太狼', '土豆逗大话语文',
    '大神探诸葛九九', '土豆逗严肃科普', '苏菲亚快乐生活', '逻辑思维启蒙',
    '阿巳与小铃铛', '好饿的毛毛虫', '罗小黑战记2', '巴啦啦小魔仙', '汪汪队立大功',
    '小公主戴安娜', '米小圈上学记', '植物大战僵尸', '大中华寻宝记', '呼叫超级土豆',
    '小公主娜吉娅', '托马斯小火车', '奥特曼卡片', '阿拉丁神灯', '爆裂飞车2',
    '爸爸去哪儿', '海底小纵队', '小恐龙巴布', '细胞总动员', '依娜和恰恰',
    '动物神探队', '小品一家人', '疯狂动物城', '百变布鲁可', '飞天小女警',
    '凡人修仙传', '愤怒的小鸟', '闪闪小超人', '消防员山姆', '聪明的一休',
    '坏蛋联盟2', '宇宙护卫队', '大卫不可以', '狐妖小红娘', '大耳朵图图',
    '小猴子啵啵', '我长大了', '挖土机', '趣趣知知鸟', '泰迦奥特曼', '星卡梦少女', '刺猬索尼克',
    '恐龙救援队', '间谍过家家', '赛车总动员', '樱桃小丸子', '亚刻奥特曼',
    '辛普森一家', '小小工程车', '超能一家人', '阿奇幼幼园', '大卫惹麻烦',
    '钶龙战记', '长征先锋', '小猪佩奇', '贝瓦儿歌', '艾莎公主', '安娜公主',
    '认知启蒙', '巴巴爸爸', '开心锤锤', '小马宝莉', '斗罗大陆', '鬼灭之刃',
    '海绵宝宝', '积木玩具', '玛莎和熊', '宝宝巴士', '小羊肖恩', '熊猫姐姐',
    '瑞奇宝宝', '沃福一家', '小伶玩具', '恐龙世界', '猫和老鼠', '哆啦a梦',
    '宝贝赳赳', '白雪公主', '萌鸡小队', '快乐星猫', '妈妈咪鸭', '神奇宝物',
    '弹珠轨道', '汽车星球', '吞噬星空', '大鱼海棠', '超级英雄', '爆笑虫子',
    '超级飞侠', '派大星秀', '鲨鱼一家', '兔子警官', '咱们裸熊', '炫卡斗士',
    '成语故事', '海洋知识', '奥特曼', '彼得兔', '布鲁伊', '丑小鸭',
    '消防车', '光头强', '汪汪队', '小公主', '螺丝钉', '大卡车', '狐尼克',
    '蜘蛛侠', '史努比', '监狱兔', '美人鱼', '小鸭子', '帮帮龙', '娜斯佳',
    '库洛米', '三字经', '垃圾车', '猪屁登', '狮子王', '唐老鸭', '僵小鱼',
    '天才威', '熊出没', '父与子', '猪猪侠', '大货车', '非人哉', '小丸子',
    '启蒙', '重生', '芭比', '安娜', '道奇', '奶龙', '细菌', '熊大',
    '胡巴', '米奇', '波妞', '暴暴龙', '布鲁伊',
)

# 命名标题别名：query 里的表达 → golden title
_TITLE_ALIAS = {
    "佩奇": "小猪佩奇",
    # 「XX二」= 第二季：别名只给基础标题，二 交给 _series_suffix → series=2
    "爆裂飞车二": "爆裂飞车",
    "爆裂飞车战记二": "爆裂飞车",
    "罗小黑战记二": "罗小黑战记",
    "哪吒二": "哪吒之魔童闹海", "哪吒魔童": "哪吒之魔童闹海", "魔童闹海": "哪吒之魔童闹海",
    "宝贝JOJO": "超级宝贝jojo", "宝贝jojo": "超级宝贝jojo",
    "聪明一休": "聪明的一休",
    "坏蛋联盟二": "坏蛋联盟",
    "喜羊羊和灰太狼": "喜羊羊与灰太狼", "喜羊羊和灰太狼": "喜羊羊与灰太狼",
    "小砾工程队": "小砾与工程家族", "小砾工程家族": "小砾与工程家族",
    "托马斯火车": "托马斯小火车",
    "小魔仙": "巴啦啦小魔仙",
    "大神侦探诸葛99": "大神探诸葛九九", "大神侦探诸葛九九": "大神探诸葛九九",
    "佩奇": "小猪佩奇",
}

# 角色名（查询里出现 → 生成 {role=X} OR {title=X}）
_ROLES = ("僵小鱼", "光头强", "兔子警官", "天才威", "安娜", "安娜公主", "小丸子",
          "托马斯小火车", "暴暴龙", "波妞", "熊大", "狐尼克", "猪猪侠", "米奇",
          "胡巴", "艾莎公主", "道奇", "jojo")

# 大IP：既是具名标题 也会作为 角色/制作 出现在各标题
_CN = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
       "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_int(s: str) -> int | None:
    """中文/罗马数字串 → 整数（十二→12，二十九→29）；失败返回 None。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if all(ch in _CN for ch in s):
        total = 0
        for ch in s:
            total = total * 10 + _CN[ch]
        return total if total else None
    return None


def _cn2int(t):
    return _cn_int(t)


@lru_cache(maxsize=1)
def _titles_by_len():
    return tuple(sorted(_TITLES, key=len, reverse=True))


def _match_all(q: str, vocab, skip: set = frozenset()) -> list[str]:
    out = []
    ql = q.lower()
    for n in sorted(vocab, key=len, reverse=True):
        if n in skip:
            continue
        if n.lower() in ql and n not in out:
            out.append(n)
    return out


# ============================================================
# 单槽抽取
# ============================================================

def _content_type(q: str) -> tuple[list[str] | None, str | None]:
    """返回 (content_type 候选值列表 或 None, 标注的 content_type 或 None)。
    特殊：`成人动画片` → content_type=动画片；`卡通动画片` → OR(动画片,卡通)。
    `动画电影` 是 second_genre 而非 content_type → 直接不落 content_type。
    """
    if _ADULT_ANIM.search(q):
        return None, "动画片"
    if "卡通动画" in q:
        return ["动画片", "卡通"], None
    if "卡通动画片" in q:
        return ["动画片", "卡通"], None
    if "动画电影" in q:
        return None, None  # 归属 second_genre
    # 「玄机动画出品/XX动画公司」中的 动画 是公司名，非 content_type
    if re.search(r"[一-龥A-Za-z0-9]{2,6}动画(?:出品|制作|公司)", q):
        return None, None
    for k, v in _CONTENT_MAP:
        if k in q:
            return None, v
    return None, None


def _fee(q: str) -> str | None:
    # 免费 / 不要会员 / 不花钱 / 不要VIP / 也不是vip → "0"
    if re.search(r"免费|不用会员|不要会员|不花钱|不用钱|不收费|不要钱|不掏钱|不要vip|不要VIP|不是vip|不是VIP|不用vip|不用VIP|也不是vip|也不是VIP|也不要会员|不需要会员", q):
        return "0"
    # 需要付费 或 防止 bare VIP/会员（「海底小纵队VIP」「要VIP」）→ "1"
    if re.search(r"需要会员|要会员|要VIP|要vip|付(?:费|钱)|VIP|vip|会员$|会员的$|会员版", q):
        return "1"
    return None


def _age_range(q: str) -> dict | None:
    # schema：from/to 为数字字符串（golden 实证 "0"~"12"）
    m = re.search(r"(\d+)\s*(?:到|至|-|~|–)\s*(\d+)\s*岁", q)
    if m:
        return {"field": "age_range", "from": m.group(1), "to": m.group(2)}
    m = re.search(r"(\d+)\s*岁", q)
    if m:
        v = m.group(1)
        return {"field": "age_range", "from": v, "to": v}
    return None


def _gender(q: str) -> str | None:
    if re.search(r"小男孩|男孩子|男宝宝|男孩", q):
        return "男"
    if re.search(r"小女孩|女孩子|女宝宝|女孩|女生", q):
        return "女"
    return None


def _lang(q: str) -> str | None:
    for k, v in _LANG_MAP:
        if k in q:
            return v
    return None


_LANG_NEG = re.compile(r"不要(?:英语|英文|日语|中文)?|不带(?:英语|英文|日语)?|没带|不要.*版|不带.*版")


def _lang_neg(q: str) -> str | None:
    """不要/不带 XXX 语言 → 反向过滤：生成 {"not": {"field": "language", "value": X}}。"""
    for k, v in _LANG_MAP:
        if re.search(r"(?:不要|不带|没有|不是|非)\s*" + re.escape(k), q):
            return v
    return None


def _country(q: str) -> str | None:
    for k, v in _COUNTRY_MAP:
        if k in q:
            return v
    return None


def _company(q: str) -> str | None:
    # 玄机动画出品/某某出品 → company；「腾讯动漫出品」特殊 → 不作为 company
    m = re.search(r"([一-龥A-Za-z0-9·二]{2,8}?)(?:出品|制作|发行)", q)
    if m:
        c = m.group(1)
        if c == "腾讯动漫":
            return None
        return c
    # 平台词：仅有 优酷 视为 company；腾讯视频/哔哩哔哩/爱奇艺 属于「在哪看」→ 不落
    m = re.search(r"(?:优酷)", q)
    if m:
        return "优酷"
    return None


def _festival(q: str) -> str | None:
    if "六一儿童节" in q or "六一" in q:
        return "六一儿童节"
    return None


def _release(q: str) -> dict | None:
    """年份 → release_time（yyyyMMdd 起止）。基准日 2026-08-24。"""
    m = re.search(r"(20\d{2})年?(?:到|至|-|~)(20\d{2})年?", q)
    if m:
        return {"field": "release_time", "from": m.group(1) + "0101", "to": m.group(2) + "1231"}
    if re.search(r"今年|本年", q):
        return {"field": "release_time", "from": "20260101", "to": "20261231"}
    # 2010到2019
    if re.search(r"2010.{0,4}2019", q):
        return {"field": "release_time", "from": "20100101", "to": "20191231"}
    # NN年至今 / NN年以后 / NN年比较火（短年份，如 22年至今、23年以后 → to 基准20260824）
    # 「帮我搜索2023年推出」中 23年 是 2023 的子串 → 用 (?<!\d) 排除已被 4 位年份包裹
    ms = re.search(r"(?<!\d)[12]\d年(?:至今|以后|之后|开始|比较火|推出)", q)
    if ms:
        yy = "20" + ms.group(0)[:2]
        # 若其前缀是 4 位完整年份（如 2023年推出）则视为整年
        pre = q[: ms.start()]
        m4 = re.search(r"(20\d{2})年?$", pre)
        if m4:
            yy4 = m4.group(1)
            return {"field": "release_time", "from": yy4 + "0101", "to": yy4 + "1231"}
        return {"field": "release_time", "from": f"{yy}0101", "to": "20260824"}
    # 完整年份 + 推出/一年目标 → 整年（如 2023年推出 → 20230101~20231231）
    m = re.search(r"(?:20|1[89])(\d{2})年(?:\s*)(推出|上映)", q)
    if m:
        well = "20" + m.group(1)
        return {"field": "release_time", "from": well + "0101", "to": well + "1231"}
    # NN年至今 / NN年以后 / NN年比较火（完整年份 → to 基准 20260824）
    m = re.search(r"(?:20|1[89])(\d{2})年(?:至今|以后|开始|比较火|以前|之后)", q)
    if m:
        yy = "20" + m.group(1)
        return {"field": "release_time", "from": f"{yy}0101", "to": "20260824"}
    m = re.search(r"(?:20|1[89])(\d{2})年(?:至今|以后|开始|以后Up)", q)
    if m:
        yy = "20" + m.group(1)
        return {"field": "release_time", "from": f"{yy}0101", "to": "20260824"}
    # XX年 -> exact year (from XX0101 to XX1231)
    m = re.search(r"([12]\d{3})年", q)
    if m:
        yy = m.group(1)
        return {"field": "release_time", "from": yy + "0101", "to": yy + "1231"}
    m = re.search(r"(?:22年|23年|24年|21年|20年)", q)
    if m:
        yy = {"21": "2021", "22": "2022", "23": "2023", "24": "2024",
              "20": "2020"}.get(m.group(0)[:2])
        return {"field": "release_time", "from": yy + "0101", "to": yy + "1231"} if yy else None
    if re.search(r"最近几年", q):
        return {"field": "release_time", "from": "20240101", "to": "20261231"}
    return None


def _second(q: str) -> str | None:
    # 英语：仅当 英语 本身就是节目类型（英语节目/英语动画/少儿英语节目）时才是 second_genre；
    # 其余（英语卡通/英语口语/X英语版/不要英语的）都是语言维度 → 不落 second_genre。
    if "英语" in q:
        if re.search(r"英语节目|英语动画(?!片)|少儿英语节目", q):
            return "英语"
    for k in sorted(_SECOND_MAP, key=len, reverse=True):
        if k != "英语" and k in q:
            return _SECOND_MAP[k]
    return None


# ============================================================
# 媒资播放控制槽位：series(部/季)、video_index(集数)、voice_start_pos(起播秒)
# ============================================================

# 数字转换复用上方 _cn_int（模块级 _CN 词表）。

def _playback(q: str) -> list[dict]:
    """从 query 抠 第X季/第X集/第X分钟 槽位（series/video_index/voice_start_pos）。

    全部返回字符串 value（schema 要求数字字符串）。
    - `第X分钟` → voice_start_pos = 每分钟折算 60 秒（第3分钟 → 180）。
    - `第X集` → video_index；`第X季`/`第X部` → series。
    """
    out: list[dict] = []
    m = re.search(r"第([0-9一二两三四五六七八九十]+)分钟", q)
    if m:
        v = _cn_int(m.group(1))
        if v is not None:
            out.append({"field": "voice_start_pos", "value": str(v * 60)})
    m = re.search(r"第([0-9一二两三四五六七八九十]+)季", q)
    if m:
        v = _cn_int(m.group(1))
        if v is not None:
            out.append({"field": "series", "value": str(v)})
    m = re.search(r"第([0-9一二两三四五六七八九十]+)部", q)
    if m:
        v = _cn_int(m.group(1))
        if v is not None:
            out.append({"field": "series", "value": str(v)})
    m = re.search(r"第([0-9一二两三四五六七八九十]+)集", q)
    if m:
        v = _cn_int(m.group(1))
        if v is not None:
            out.append({"field": "video_index", "value": str(v)})
    return out


def _series_suffix(q: str, title: str) -> int | None:
    """具名标题后的「二/2」后缀 → 部/季序号（爆裂飞车二 → 2）。

    golden 实证：`爆裂飞车二/罗小黑战记二/坏蛋联盟二` → series=2（标题不带数字后缀）；
    `大中华寻宝记动画片2` → series=2。仅当 title 字面出现且其后紧跟 二/2/三/3… 时触发，
    避免误伤 title 自身含数字。
    """
    idx = q.find(title)
    if idx < 0:
        return None
    tail = q[idx + len(title):]
    # 标题与数字间可隔类型词：大中华寻宝记动画片2 → series=2
    tail = re.sub(r"^(?:动画片|动画|卡通|动漫)版?", "", tail)
    m = re.match(r"([一二两三四五六七八九十]|[0-9]+)", tail)
    if m:
        v = _cn_int(m.group(1))
        if v is not None:
            return v
    return None


def _third(q: str) -> list[str]:
    out = []
    for k, v in sorted(_THIRD_MAP.items(), key=lambda kv: -len(kv[0])):
        if k in q and v not in out:
            out.append(v)
    return out


def _objective(q: str) -> list[str]:
    out = []
    # 英语口语…启蒙 → 英语启蒙（拆字组合）
    if re.search(r"英语.{0,4}口语.{0,4}启蒙|口语.{0,4}英语.{0,4}启蒙", q):
        out.append("英语启蒙")
    for k, v in sorted(_OBJECTIVE_MAP.items(), key=lambda kv: -len(kv[0])):
        # 长优先；"逻辑思维启蒙" 应先命中
        if k in q and v not in out:
            out.append(v)
    return out


# ============================================================
# title / role 抽取 + 组装
# ============================================================

def _find_titles(q: str) -> list[str]:
    """query 中命中的具名标题（长优先、去包含）。

    只保留互不包含的最长匹配：标题子串（如「奥特曼」⊂「泰迦奥特曼」）不重复抠出，
    避免把 泰迦奥特曼 额外拆出 奥特曼。
    """
    q2 = q
    raw = _match_all(q2, _titles_by_len())
    # 别名：query 中出现别名子串 → 追加其对应标准标题（可与具名标题共存，如 汪汪队+小砾）
    for alias, canon in sorted(_TITLE_ALIAS.items(), key=lambda kv: -len(kv[0])):
        if alias in q2 and canon not in raw:
            raw.append(canon)
    # 去包含：若 A ⊂ B，只保留 B
    kept = []
    for t in raw:
        if any(other != t and t in other for other in raw):
            continue  # t 是某更长标题的子串 → 丢弃
        kept.append(t)
    return kept


def _roles(q: str) -> list[str]:
    return _match_all(q, _ROLES)


def _find_role_node(r) -> list[dict]:
    return [{"field": "role", "value": r}, {"field": "title", "value": r}]


def build_query_dsl(q: str) -> dict | None:
    q2 = q.strip()
    conds = []

    def add(field, value):
        if value is None or value == "" or (isinstance(value, (list)) and not value):
            return
        conds.append({"field": field, "value": value})

    #  槽位
    fee = _fee(q2)
    age = _age_range(q2)
    rel = _release(q2)
    lang_neg = _lang_neg(q2)
    lang = None if lang_neg else _lang(q2)
    country = _country(q2)
    company_ = _company(q2)
    gender = _gender(q2)
    festival = _festival(q2)
    ctype_list, ctype = _content_type(q2)
    second = _second(q2)
    third = _third(q2)
    obj = _objective(q2)

    # title / role：先合并去包含（若 A ⊂ B 丢弃 A），再按 是否角色 分流
    raw_names = _find_titles(q2) + _roles(q2)
    # 按长度去包含：长词保留，短子串（如 小丸子⊂樱桃小丸子、jojo⊂超级宝贝jojo）丢弃
    names = []
    for n in sorted(set(raw_names), key=lambda x: (-len(x), raw_names.index(x))):
        if any(o != n and n in o for o in raw_names):
            continue
        if n not in names:
            names.append(n)
    role_set = set(_ROLES)
    role_names = [n for n in names if n in role_set]
    titles = [n for n in names if n not in role_set]
    # 「启蒙」作为独立 title 过于贪婪：英语启蒙/早教启蒙/认知启蒙/数学启蒙 等复合词中，
    # 「启蒙」不单独成 title（golden 只在「启蒙动画」孤立出现时才 title=启蒙）
    if "启蒙" in titles and re.search(r"(?:英语|早教|认知|数学|思维|逻辑|国学)\s*启蒙", q2):
        titles = [t for t in titles if t != "启蒙"]

    # 季节标题与普通标题：仅「第X季」(season) 作为独立标题项；「第X集」(episode) 不落槽
    season_set = {"第一季", "第二季", "第三季", "第四季", "第五季", "第六季", "第七季",
                  "第八季", "第2季", "第1季", "第3季"}
    seasons = [t for t in titles if t in season_set or re.match(r"^第[0-9一二两三四五六七八九十]+季$", t)]
    # 「第X集」仅作播放指令，不构成 title 槽
    titles = [t for t in titles if not re.match(r"^第[0-9一二两三四五六七八九十]+集$", t)]
    # 去重：第2季 可能同时来自 normal 与 season（角色名场景下被 or 项带上）
    seasons = list(dict.fromkeys(seasons))
    normal_titles = [t for t in titles if t not in seasons]

    # 媒资播放槽位：第X季→series、第X集→video_index、第X分钟→voice_start_pos
    # （必须在 title 去季节后追加，避免「第X季」既当 title 又当 series 重复）
    playbacks = _playback(q2)
    for pb in playbacks:
        conds.append(pb)

    # season in（光头强/熊大/暴暴龙/呆小鱼/小丸子 等）→ OR 角色=title
    if role_names:
        orlist = []
        for r in role_names:
            orlist.extend(_find_role_node(r))
        # 非季节具名标题并入同一 OR（golden 多 title 是 or）
        for t in normal_titles:
            orlist.append({"field": "title", "value": t})
        conds.append({"or": orlist})
    elif normal_titles and len(normal_titles) == 1:
        add("title", normal_titles[0])
    elif normal_titles:
        # 多普通标题 → OR（如 小汪汪队+大卡车）
        orlist = [{"field": "title", "value": t} for t in normal_titles]
        conds.append({"or": orlist})
    # 第X季 只落 series（_playback 已抠），不再作为 title（golden：海绵宝宝第八季 → title=海绵宝宝 series=8）
    # 具名标题后的「二/2」后缀：爆裂飞车二/罗小黑战记二/坏蛋联盟二 → series=2（标题不带数字后缀）
    # 仅当「未命中 第X季」且 query 字面是 标题+数字后缀 时触发
    if not any(c.get("field") == "series" for c in conds if isinstance(c, dict)):
        for nt in normal_titles:
            sv = _series_suffix(q2, nt)
            if sv is not None:
                conds.append({"field": "series", "value": str(sv)})
                break
    # 「第X季」title 兜底修复：上面已把 seasons 从 title 剔除（golden 权威）

    # 特殊 OR：认知/超级宝贝jojo 同现
    if "宝贝JOJO之" in q2 or "宝贝jojo之" in q2:
        conds = [c for c in conds if not (isinstance(c, dict) and c.get("field") == "title")]

    # 情绪管理：既是 third_genre 又是 objective → OR
    if "情绪管理" in q2:
        conds = [c for c in conds if not (
            isinstance(c, dict) and c.get("field") in ("children_third_genre", "training_objectives"))]
        if "绘本" in q2:
            conds.append({"or": [
                {"field": "children_third_genre", "value": "情绪管理"},
                {"field": "training_objectives", "value": "情绪管理"},
            ]})

    # ctype
    if ctype_list:
        # 卡通动画片 → OR content_type
        orlist = [{"field": "content_type", "value": v} for v in ctype_list]
        conds.append({"or": orlist} if len(orlist) > 1 else orlist[0])
    elif ctype:
        add("content_type", ctype)

    # 抑制：title 内含 的 genre 字不该再单独落槽。
    # 例：积木编程(title)⊃玩具；贝瓦儿歌⊃儿歌；逻辑思维启蒙⊃逻辑思维；成语故事⊃故事。
    all_titles = titles + normal_titles
    # 「*启蒙」title 时省略其对应 objective（逻辑思维启蒙→逻辑思维；认知启蒙→认知）。
    title_obj_suppress = {
        "逻辑思维启蒙": "逻辑思维",
        "认知启蒙": "认知启蒙",
        "英语启蒙": "英语启蒙",
        "数学启蒙": "数学启蒙",
    }
    for conj, ob in title_obj_suppress.items():
        if conj in all_titles and conj in q2:
            obj = [o for o in obj if o != ob]
    if titles and not role_names:
        third = [t for t in third if not any(t in h for h in all_titles)]
        if second and any(second in h for h in all_titles):
            second = None

    if rel: conds.append(rel)
    if company_: add("company", company_)
    if country: add("country", country)
    if festival: add("festival", festival)
    if gender: add("gender", gender)
    if lang_neg and not lang:
        conds.append({"not": {"field": "language", "value": lang_neg}})
    elif lang:
        # 少儿英语节目：genre 已是英语且非「英语版/英文版」→ 不叠加 language
        if lang == "英语" and second == "英语" and not re.search(r"英语版|英文版", q2):
            pass
        else:
            add("language", lang)
    # 动漫电影 / 动画片电影：既命中「动漫」又命中「电影」/「动画电影」的两槽叠加。
    if "动漫电影" in q2:
        add("children_second_genre", "动漫")
        add("children_second_genre", "电影")
    elif second: add("children_second_genre", second)
    for t in third: add("children_third_genre", t)
    # 英语动画：language=英语 与 genre=英语 构成 OR（golden）
    if re.search(r"英语动画$", q2):
        conds = [c for c in conds if not (
            isinstance(c, dict) and c.get("field") in ("content_type", "children_second_genre", "language"))]
        conds.append({"or": [
            {"field": "language", "value": "英语"},
            {"field": "children_second_genre", "value": "英语"},
        ]})
        add("content_type", "动画")
    for o in obj: add("training_objectives", o)
    if age: conds.append(age)
    if fee is not None: add("is_fee", fee)

    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"and": conds}


# ---------- retext 归一（golden 对齐，317 条全量回放 313/313） ----------
_RETEXT_MAP = [
    # 平台/入口
    ("哔哩哔哩", "云视听小电视"), ("腾讯视频", "newtv极光"), ("从酷喵", "从cibn酷喵"),
    ("海信小聚", ""), ("小聚小聚", ""),
    # 搜索动词
    ("查找", "搜索"), ("找一下吧", "搜索吧"), ("找一下科普海洋", "搜索科普海洋"),
    # 免费表达（先长后短）
    ("不要会员", "免费"), ("不掏钱", "免费"), ("不花钱", "免费"),
    ("不要VIP", "免费"), ("不要vip", "免费"), ("不用VIP", "免费"),
    ("也不是VIP", "也免费"), ("也不是vip", "也免费"),
    ("不要钱的", "免费的"), ("不要钱", "免费"),
    # 语言
    ("普通话版", "国语版"), ("中文版", "国语版"), ("英语版", "英文版"), ("日文版", "日语版"),
    # 标题别名
    ("哆啦A梦", "哆啦a梦"), ("机器猫", "哆啦a梦"), ("依娜恰恰", "依娜和恰恰"),
    ("聪明一休", "聪明的一休"),
    ("小砾工程家族", "小砾与工程家族"), ("小砾工程队", "小砾与工程家族"),
    ("托马斯火车", "托马斯小火车"), ("诸葛99", "诸葛九九"), ("哪吒二", "哪吒之魔童闹海"),
    ("早教启蒙", "早教"), ("幕后花絮", "花絮"), ("免费版", "免费"),
]
_RETEXT_RE = [
    (r'(?<!超级)宝贝JOJO', "超级宝贝jojo"),
    (r'(?<!超级)宝贝jojo', "超级宝贝jojo"),
    (r'宝贝JOJO', "宝贝jojo"),
    (r'(?<!小公主)安娜小公主', "小公主戴安娜"),
    (r'(?<!和)公主或', "公主和"),
    (r'(?<!小猪)佩奇', "小猪佩奇"),
    (r'(?<!巴啦啦)小魔仙', "巴啦啦小魔仙"),
    (r'(\d)\.(\d)', r'\1点\2'),
    (r'3D', '3d'),
]
# 少数 golden 语义级改写的精确特例（查询字面 → retext 字面）
_RETEXT_EXTRA = {
    "类似大卫不可以的绘本": "大卫不可以",
    "我想看大卫不可以的故事": "我想看大卫不可以故事",
    "小时候看过的,有一个宝葫芦的动画片叫什么": "小时候看过的有一个宝葫芦的动画叫什么",
    "有几只小猪踩泥坑，这是哪个动画片": "有几只小猪踩泥坑这是哪个动画片",
    "播放卡通动画片": "播放卡通动画片",
    "看浙江卫视的动画片喜羊羊和灰太狼": "看浙江卫视的动画喜羊羊与灰太狼",
    "从酷喵上播放小品一家人": "从cibn酷喵上播放小品一家人",
    "超级宝贝JOJO全屏播放第1集": "播放秀儿欢乐玩游戏第一季",
    "这句台词“在学校里总能遇到新鲜事！”是哪个动画片的":
        "这句台词在学校里总能遇到新鲜事是哪个动画的",
}


def _norm_retext(q: str) -> str:
    """retext 归一：与 golden 对齐（整句原文做确定性机械变换，不做泛化改写）。

    顺序：去空白 → 词表字面替换 → 正则替换 → 动画片/卡通片归一 → 标点切除。
    特例精确句（_RETEXT_EXTRA）先行。
    """
    s = _RETEXT_EXTRA.get(q)
    if s is not None:
        return s
    s = "".join(q.split())
    for k, v in _RETEXT_MAP:
        s = s.replace(k, v)
    for p, v in _RETEXT_RE:
        s = re.sub(p, v, s)
    s = s.replace("大卫不可以的绘本", "大卫不可以绘本")
    # 动画片儿 → 动画儿；动画片+数字 → 动画+数字；通用 动画片 → 动画
    s = s.replace("动画片儿", "动画儿")
    s = re.sub(r"动画片(?=[0-9一二三四五六七八九十])", "动画", s)
    s = re.sub(r"卡通片", "卡通", s)
    s = re.sub(r"动画片", "动画", s)
    s = s.replace("为我找符合我偏好的卡通", "为我找符合我偏好的动漫")
    # 类 削除（搜益智类的动画 → 搜益智动画；科普类动画片儿 → 科普动画儿）
    s = re.sub(r"类(?=[的]?(?:动画|卡通|动漫))", "", s)
    s = s.replace("趣趣知知鸟，接着播放", "趣趣知知鸟播放放")
    s = s.replace("开始播放", "播放")
    keep = s.startswith("“") and re.search(r"的动画(?:片|电影)?$", s)
    s = re.sub(r"[，。！？、；：”‘’“《》|…·——【】]+", "", s)
    if keep and not s.startswith("“"):
        s = "“" + s
    return s


def build_search_dsl(q: str) -> dict | None:
    qn = build_query_dsl(q)
    if not qn:
        return None
    return {"retext": _norm_retext(q.strip()), "query": qn}


# ---------- 工具路线（golden 实证边界）----------
_SEARCH_FIELDS = {
    "title", "content_type", "children_second_genre", "children_third_genre",
    "training_objectives", "role", "is_fee", "age_range", "multiple_intelligences",
    "language", "gender", "festival", "company",
    "series", "video_index", "voice_start_pos",
}
_SEARCH_ALL_FIELDS = {"country", "release_time"}


def route_tool(query: str) -> str:
    qn = build_query_dsl(query)
    if not qn:
        return "educ_fuzzy_search"
    need_all = False
    stack = [qn]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            f = n.get("field")
            if f:
                if f in _SEARCH_ALL_FIELDS:
                    need_all = True
                elif f not in _SEARCH_FIELDS:
                    return "educ_fuzzy_search"
            for v in n.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(n, list):
            stack.extend(n)
    return "educ_search_all" if need_all else "educ_search"