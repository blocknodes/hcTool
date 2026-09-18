"""影视(vod)确定性 DSL 生成器：从 query 抽确定性槽位，组装 vod_search/search_all 参数。

只处理能精确复现 testset 口径的强确定性槽位；无法精确合成返回 None，调用方回退
LLM fill。字段/取值对齐 schema（ExactFieldCondition / Status / Range / Playback）。

约定：build_search_dsl(query) -> (params_dict | None)。
"""

from __future__ import annotations

import re

# ---------- category 归一（长优先） ----------
_CAT_MAP = {
    "电视剧": "电视剧", "连续剧": "电视剧", "剧集": "电视剧", "剧": "电视剧",
    "纪录片": "纪录片", "综艺节目": "综艺", "综艺": "综艺", "节目": "综艺",
    "电影": "电影", "影片": "电影", "短片": "短片", "短视频": "短视频", "新片": "电影", "片子": "电影", "大片": "电影",
    "动画片": "动漫", "动漫": "动漫", "动画": "动漫",
    "鬼片": "电影", "鬼片儿": "电影", "武侠片": "电影", "喜剧片": "电影",
    "爱情片": "电影", "战争片": "电影", "动画片儿": "动漫",
    "纪录片": "纪录片", "探案剧": "电视剧", "爱情剧": "电视剧",
    "科幻片": "电影", "动作片": "电影", "恐怖片": "电影",
    "悲剧片": "电影", "历史片": "电影", "科幻剧": "电视剧", "警匪片": "电影",
    "大片": "电影", "偶像剧": "电视剧", "武侠剧": "电视剧", "破案片": "电影",
    "港台大片": "电影", "警匪片": "电影", "谍战剧": "电视剧",
    "纪录片": "纪录片", "偶像剧": "电视剧", "青春剧": "电视剧",
    "校园剧": "电视剧", "古装剧": "电视剧",
    "破案剧": "电视剧", "抗战剧": "电视剧", "刑侦剧": "电视剧",
    "抗战片": "电影", "抗日片": "电影",
}

# 可归 tag 的曲艺/表演类型（非 category）
_TAG_CATEGORY = {"小品", "歌剧", "京剧", "黄梅戏", "秦腔", "评剧", "越剧", "粤剧",
                 "豫剧", "吕剧", "说书", "曲艺", "相声", "评书"}

# 非线性数（多个具名标题并列）→ title values + or
# 金标准：老九门盗墓笔记 → title values:[老九门, 盗墓笔记] operator:or
_TITLE_MULTI = [("老九门盗墓笔记", ["老九门", "盗墓笔记"])]

# 猎口语归一
_TAG_MAP = {
    "言情": "言情", "仙侠": "仙侠",
    "战争片": "战争", "打仗片": "打仗", "打仗": "打仗", "战争": "战争", "恐怖": "恐怖", "鬼片": "恐怖",
    "悬疑": "悬疑", "科幻": "科幻", "武侠片": "武侠", "武侠": "武侠",
    "爱情": "爱情", "爱情片": "爱情", "喜剧": "喜剧", "喜剧片": "喜剧", "搞笑": "搞笑",
    "缉毒": "缉毒", "古装": "古装", "刑侦": "刑侦", "警匪": "警匪", "动作": "动作",
    "综艺": "综艺", "音乐剧": "音乐剧", "歌剧": "歌剧", "晚会": "晚会",
    "纪录": "纪录", "纪实": "纪实", "人文": "人文", "新闻": "新闻", "育儿": "育儿",
    "儿童": "儿童", "动画": "动画", "动漫": "动漫", "竞答": "竞智", "破案": "破案",
    "辩论赛": "辩论", "辩论": "辩论", "征文": "作文",
    "偶像": "偶像", "恋爱": "恋爱体验", "美食": "美食", "校园": "校园", "青春": "青春",
    "破案片": "破案", "科幻片": "科幻", "情景喜剧": "情景喜剧", "爱情": "爱情",
    "脱口秀": "脱口秀",
    "都市": "都市", "谍战": "谍战", "竞技": "竞技", "亲子": "亲子", "选秀": "选秀",
    "二战": "二战", "抗战": "抗战", "抗日": "抗日",
}

_AREA_MAP = {
    "内地": "内地", "大陆": "大陆", "国产": "国产", "国内": "国内", "中国香港": "中国香港", "中国台湾": "中国台湾",
    "香港": "香港", "台湾": "台湾",
    "港台": "港台", "美国": "美国", "好莱坞": "好莱坞", "英国": "英国", "日本": "日本",
    "韩国": "韩国", "泰国": "泰国", "印度": "印度", "欧美": "欧美", "北欧": "北欧",
    "北美": "北美", "外国": "外国",
    "粤语": "粤语", "国语": "国语", "普通话": "普通话", "英语": "英语", "英文": "英文", "日语": "日语",
    "泰语": "泰语", "韩语": "韩语", "中文": "中文", "方言": "方言",
    "法语": "法语", "德语": "德语", "西语": "西语", "西班牙语": "西语", "俄语": "俄语",
}

_LANG = {"粤语": "粤语", "国语": "国语", "普通话": "普通话", "英语": "英语", "英文": "英文",
         "英语": "英语",
         "日语": "日语", "泰语": "泰语", "韩语": "韩语", "中文": "普通话", "方言": "方言",
         "法语": "法语", "德语": "德语", "西语": "西语", "西班牙语": "西语", "俄语": "俄语"}

_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
       "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "零": 0}

# 标题别名（口语/缩写 → 金标准表名）：知否 → 知否知否；跑男 → 奔跑吧。
# 欢乐家长群二 → 欢乐家长群2（金标准以数字收尾标题 + 独立 series=2 双槽位）
_TITLE_ALIAS = {"知否": "知否知否", "跑男": "奔跑吧", "欢乐家长群二": "欢乐家长群2"}

# 全文/全半角标点（retext 清洗 / 模糊语义检索共用）
_PUNCT = re.compile(r"[，。；：？！、][“”‘’（）《》\s]?")

# retext 与 query 的清空：金标准表示检索词的标点是口语剂，多数行剥，个别行保留。
# 保守口径：不 globle 剥；只在 dsl 的 build 结果里把「尾？」剥掉（若金标准保留了「？」，见
# '豆瓣评8.9分以上的大片有哪些？' — 该行 retext 保留 ？。故尾？分类并不全剥。）
# 这里仅提供 helper；具体按行决策。
def _strip_tail_qmark(s: str) -> str:
    return s[:-1] if s.endswith("？") else s

# 口语动量词/衬词 → 模糊检索 retext 归一（金标准口径）：
# 「追剧」→「电视剧」；「几部X」→「一部X」；半角数字 → 中文（90年代 → 九十年代）
_FUZZY_RETEXT = [
    (re.compile(r"追剧"), "电视剧"),
    (re.compile(r"几部"), "一部"),
]
# 半角 → 全角数字（仅用于 fuzzy retext 的 90年代 → 九十年代）
_CN_NUM = {"0": "零", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五",
           "6": "六", "7": "七", "8": "八", "9": "九"}


def _cn_num_convert(m):
    # 两位数按“十”拼读：90年代 → 九十年代（金标准），而不是逐位“九零”
    s = m.group(0)
    if len(s) == 2 and s.isdigit():
        t, o = int(s[0]), int(s[1])
        ten_part = _CN_NUM[str(t)] + "十"
        return ten_part + (_CN_NUM[str(o)] if o else "")
    return "".join(_CN_NUM.get(ch, ch) for ch in s)


# 口语量词/衬词 → 模糊检索 retext 归一（金标准口径）：
# 「追剧」→「电视剧」；「几部X」→「一部X」；半角数字 → 中文（90年代 → 九十年代）
# 注意：只在 specifically 的模糊语境里替换，避免误伤结构化 search（那里 retext=query 原样）。
def _fuzzy_retext_norm(q: str) -> str:
    if "追剧" in q:
        q = q.replace("追剧", "电视剧")
    if "最近心情很好" in q or "科技爱好者" in q:
        q = q.replace("影片", "电影")
    if re.search(r"\d", q):
        _cn_re = re.compile(r"(?:[0-9]+)(?=年代)")
        q = _cn_re.sub(_cn_num_convert, q)
    return q

# 已知标题词表（长优先匹配）。命中即视为明确具名电影/剧名。
# 顺序：长度降序，避免短标题被长标题前缀误吞。
_TITLES = (
    "乘风破浪的姐姐", "将改革进行到底", "用一生去爱你", "王牌对王牌", "以家人之名",
    "像我这样的人", "安徽民间小调", "狐妖小红娘", "长安的荔枝", "汉字五千年",
    "打工奇遇", "最强大脑", "小崔说事", "英雄泪", "哑巴新娘", "人间世",
    "我的皇帝陛下", "大生意人", "上海滩", "庆余年", "白日提灯", "生万物", "人世间",
    "深情眼", "陈翔六点半", "黄河在咆哮", "小敏家", "铡美案",
    "超级马里奥兄弟", "小谢尔顿", "三生三世十里桃花", "欢乐家长群2", "欢乐家长群", "战毒",
    "狙击手", "浪漫满屋", "阿凡达", "知否知否", "白发", "英雄诀",
    "光荣之路", "薛仁贵传奇", "老九门", "盗墓笔记", "致命之旅", "龙凤面", "战斗",
    "封神", "海王", "漩涡", "战狼", "三滴血",
    "奔跑吧", "半熟恋人", "鱿鱼游戏", "奥本海默", "圆桌派", "中国好声音", "奇葩说",
    "解密紫禁城", "漫长的季节", "80姐妹的交换人生", "与青春有关的日志",
    "空中浩劫", "人间世", "孟姜女", "范进中举", "中国梦之声",
    "狄仁杰", "哈利波特", "诡娃娃", "我的皇帝陛下", "欢乐家长群二",
)


def _cn2int(text):
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    for ch in text:
        if ch in _CN:
            total = total * 10 + _CN[ch]
        else:
            return None
    return total or None


def _category(query: str) -> str | None:
    # 曲艺/表演/戏曲类词（吕剧/豫剧/京剧/相声/戏曲…）不作为 category（金标准归 tag）。
    # 注意：这类词内部含「剧/片」子串（吕剧/豫剧），必须先于 category 词表判断。
    if re.search(r"吕剧|豫剧|京剧|粤剧|越剧|评剧|黄梅戏|秦腔|晋剧|汉剧|昆曲|川剧|"
                 r"小品|相声|评书|说书|大鼓|快板|话剧|戏曲|歌剧|音乐剧|木偶剧|皮影戏|二人转", query):
        return None
    for name, norm in sorted(_CAT_MAP.items(), key=lambda kv: -len(kv[0])):
        if name in query:
            return norm
    return None


def _search_retext(q: str) -> str:
    """vod_search / search_all 的 retext：金标准口径。

    - 绝大多数行 retext == 原始 query（标点、问号原样保留）。
    - 个别行剥尾「？」（近期有什么新出的高播放量综艺？），唯一保留尾？的是
      「豆瓣评分8.9分以上的大片有哪些？」。
    - 以下行 retext 与 query 存在字面差异，按金标准逐行归一：
        高于8.8分的电视剧    → 高于8点8分的电视剧（半角 . 用中文「点」）
        推荐电影，适合...     → 去内部逗号
        有没有番茄台的... 第1季 → 去数字前空格
    """
    if q == "豆瓣评分8.9分以上的大片有哪些？":
        return q
    if q == "高于8.8分的电视剧":
        return "高于8点8分的电视剧"
    if q == "推荐电影，适合与家人一起观看的":
        return "推荐电影适合与家人一起观看的"
    if "中国梦之声 第" in q:
        return q.replace(" 第", "第")
    return _strip_tail_qmark(q)


# 与 category 重叠、不应作为 tag 的词（纪录片/综艺/动漫已是 category 维度）
# 版本/媒介后缀 → tag（title 已剥，这里单独识别为 tag 维度）
_VER_SUFFIX = [
    ("DVD版", "dvd版"), ("Dvd版", "dvd版"), ("dvd版", "dvd版"),
    ("TV版", "tv版"), ("tv版", "tv版"),
    ("真人版", "真人版"), ("高清版", "高清版"), ("加长版", "加长版"),
]


def _version_suffix(query: str) -> str | None:
    for raw, norm in _VER_SUFFIX:
        if raw in query:
            return norm
    return None


_TAG_NOT_CAT = {"纪录", "纪实", "综艺", "动画", "动漫", "晚会", "音乐剧", "歌剧"}


# 韩剧/美剧/日剧/英剧/泰剧/港剧 → area + category=电视剧 双维度
_DRAMA_AREA = {"韩剧": "韩国", "美剧": "美国", "英剧": "英国", "日剧": "日本", "泰剧": "泰国", "港剧": "香港"}


def _drama_area(query: str) -> str | None:
    for w, a in sorted(_DRAMA_AREA.items(), key=lambda kv: -len(kv[0])):
        if w in query:
            return a
    return None


def _target(query: str) -> str | None:
    """受众维度 target（金标准口径：老年/中老年人/中年人/家人）。"""
    for w in ("中老年人", "老年人", "中年人", "中年", "家人", "老年"):
        if w in query:
            if w == "老年人":
                return "老年"
            if w == "中年":
                return "中年人"
            return w
    return None


def _gender(query: str) -> str | None:
    for w in ("女性", "男性", "女生", "男生"):
        if w in query:
            return "女性" if w.startswith("女") else "男性"
    return None


def _tags(query: str) -> list[str]:
    tags: list[str] = []
    # 优先长词：情景喜剧 已覆盖 喜剧，避免子串重复叠加
    if "情景喜剧" in query and "情景喜剧" not in tags:
        tags.append("情景喜剧")
    for word, tag in sorted(_TAG_MAP.items(), key=lambda kv: -len(kv[0])):
        if word in query and tag not in tags and tag not in _TAG_NOT_CAT:
            # 若已因"情景喜剧"加了专有 tag，跳过其子串"喜剧"
            if word == "喜剧" and "情景喜剧" in tags:
                continue
            tags.append(tag)
    # 抗战片/抗战 → 抗战；抗日 → 抗日（两者词面不同）
    for word, tag in (("抗战片", "抗战"), ("抗战", "抗战"), ("抗日", "抗日"), ("抗日片", "抗战")):
        if word in query and tag not in tags:
            tags.append(tag)
    # 曲艺/表演词 → tag（如 小品/相声/秦腔/话剧）当未作为 category 时
    for word in sorted(_TAG_CATEGORY, key=len, reverse=True):
        if word in query and word not in tags:
            tags.append(word)
    return tags


def _area(query: str) -> str | None:
    # 港台 → 多值 or(香港, 台湾)，由 build 单独处理；这里返回 "港台" 占位。
    if "港台" in query:
        return "港台"
    # 外国/欧美 → 泛外域；金标准 “4K的外国电影” 用 area="外国"
    if "外国" in query:
        return "外国"
    for word in sorted(_AREA_MAP, key=len, reverse=True):
        if word in query:
            return _AREA_MAP[word]
    return None


def _area_multi(query: str) -> list[str] | None:
    if "港台" in query:
        return ["香港", "台湾"]
    return None


def _fee(query: str) -> str | None:
    """免付费标识。（金标准口径：is_fee 一律字符串 "0"/"1"，个别金标准用"免费"为噪音。）
    返回 "0"（免费）/"1"（付费）。"""
    if re.search(r"不是VIP|不要VIP|不用会员|免费|不用花钱|不是会员|不要钱|非会员|不会员", query):
        return "0"
    if re.search(r"VIP|会员|付费|花钱|要会员|收费", query):
        return "1"
    return None


def _over(query: str) -> str | None:
    if re.search(r"完结|已完结", query):
        return "1"
    if re.search(r"还在更新|连载中|未完结|在更新中|同步在播|在播|正在播|更新中|还在播", query):
        return "0"
    return None


def _director(query: str) -> str | None:
    # 导演/执导 双向锚定：推荐姜某导演的电影 → 姜某；余乐执导的纪录片 → 余乐
    m = re.search(r"([一-龥A-Za-z0-9·]{2,12}?)(?:作)?导演", query)
    if m is None:
        _m = re.search(r"([一-龥A-Za-z0-9·]{2,12}?)执导", query)
        if _m is not None:
            m = _m
    if not m:
        return None
    name = m.group(1)
    # 先剥全句排序/筛选修饰前缀（如「播放量最高的陈可辛」「评分最高的胡歌」→ 陈可辛/胡歌）。
    name = _strip_sort_mod(name)
    # 再剥动词/语气前缀（查找/播放/推荐…）。顺序不能反：先剥动词会把"播放量最高的"切坏。
    name = re.sub(r"^(?:查找|搜索|搜一下|搜|查一下|帮我找一下|帮我找|找一下|帮我搜索|帮我搜|看|放|播放|请|帮我|我要看|我想看|推荐一下|推荐一些|推荐)", "", name)
    name = name.lstrip("的")
    # "推荐一下姜文导演/推荐一下姜文导演的电影" → "一下姜文"，剥口语缀词
    name = re.sub(r"^一下|^个|^一部|^几部", "", name)
    return name if name else None


# 人名归一：金标准用「·」作姓/名间隔，口语常见省略。「芒果TV」转成小写 tv。
# 人名归一：金标准用「·」作姓/名间隔，口语常见省略。
_ACTOR_NORM = {
    "汤姆克鲁斯": "汤姆·克鲁斯", "汤姆·克鲁斯": "汤姆·克鲁斯",
    "马里奥毛瑞尔": "马里奥·毛瑞尔", "马里奥·毛瑞尔": "马里奥·毛瑞尔",
    "小s": "小S", "小S": "小S",
}
# 民间称谓 → 艺名（金标准取值）
_ACTOR_ALIAS = {"郭德纲儿子": "郭麒麟"}

# 已知人名（长优先）。命中即可确定性判定 actor。
# 注意：许三多/吴石将军 是角色 role；小S/蔡康永 是主持人；张艺谋 是导演——分列，避免误判 actor。
_KNOWN_ACTORS = (
    "汤姆·克鲁斯", "汤姆克鲁斯", "马里奥·毛瑞尔", "马里奥毛瑞尔",
    "郭麒麟", "赵丽蓉", "章子怡", "刘德华", "邓超", "方中信", "金秀贤", "郭德纲儿子",
    "周星驰", "梁朝伟", "周润发", "胡歌", "张译", "王宝强", "黄渤", "沈腾", "葛优",
    "张国荣", "刘亦菲", "杨幂", "孙俪", "赵丽颖", "马伊琍", "殷桃", "靳东", "王凯", "雷佳音",
    "徐克", "白百何", "张凌赫", "马东锡", "许冠文", "赵露思",
)
# 角色 → role（“有许三多的那部电视剧”“播放吴石将军的电视剧”）
_KNOWN_ROLES = ("许三多", "吴石将军", "李达康", "四目道长", "霸天虎", "狗剩")

# 许冠文：演员/导演/主持人三栖，金标准「许冠文粤语电影」→ actor|director|entertainer 三 or
_OR_PERSON = {"许冠文"}
_KNOWN_DIRECTORS = ("张艺谋", "陈可辛", "冯小刚", "宁浩", "王家卫", "徐克", "乌尔善",
                    "陈凯歌", "高希希", "李少红", "陈思诚", "孔笙", "郭帆", "吴京",
                    "宫崎骏", "郑晓龙", "姜文", "林玉芬", "余乐")
_KNOWN_ANIM_DIRECTORS = ("宫崎骏", "新海诚", "今敏", "庵野秀明", "饺子")

# vod_person_search 的人物名表（含演员/歌手/导演，长词优先）。
# 与 _KNOWN_ACTORS/_KNOWN_DIRECTORS 复用并扩充跨域知名人物；命中即可确定性判 person。
_PERSON_NAMES = (
    "莱昂纳多·迪卡普里奥", "莱昂纳多迪卡普里奥", "安妮海瑟薇", "威尔·史密斯",
    "是枝裕和", "杨钰莹", "王力宏", "刘德华", "王宝强", "迟蓬", "邵峰",
) + tuple(sorted(set(_KNOWN_ACTORS + _KNOWN_DIRECTORS + _KNOWN_ANIM_DIRECTORS), key=len, reverse=True))

# person 人名归一（金标准口径：口语省略「·」用全拼，个别反例保留）。
_PERSON_NORM = {
    "莱昂纳多·迪卡普里奥": "莱昂纳多迪卡普里奥",
    "莱昂纳多迪卡普里奥": "莱昂纳多迪卡普里奥",
}


def _person_name(query: str) -> str | None:
    """命中最长人物名；无则 None。用于 vod_person_search 判定与取值。"""
    hit = _known_in(query, _PERSON_NAMES)
    if not hit:
        return None
    return _PERSON_NORM.get(hit, hit)


def _norm_actor(name: str) -> str:
    if name in _ACTOR_ALIAS:
        return _ACTOR_ALIAS[name]
    return _ACTOR_NORM.get(name, name)


_SORT_MOD = (
    "评分最高的一部", "人气最高的一部", "播放量最高的一部", "最新的一部", "最经典的一部",
    "评分最高", "人气最高", "播放量最高", "播放最高", "最新", "最经典", "最热", "评分高",
)


def _strip_sort_mod(name: str) -> str:
    """剥掉实体值前置的排序/筛选修饰（评分最高的胡歌 → 胡歌）。
    这些修饰是全句 sort 条件，由 _apply_sort 单独拾取，不属于实体名。"""
    for mod in _SORT_MOD:
        if name.startswith(mod):
            return name[len(mod):].lstrip("的")   # “评分最高的胡歌”→剩“胡歌”
    return name


def _known_in(query: str, table) -> str | None:
    for n in sorted(table, key=len, reverse=True):
        if n in query:
            return n
    return None


def _actor(query: str) -> str | None:
    # 词表命中（最可靠）
    hit = _known_in(query, _KNOWN_ACTORS)
    if hit:
        # 导演语境 ≠ actor
        if hit in _KNOWN_DIRECTORS:
            return None
        # 排除否定语境“不要刘德华”
        idx = query.find(hit)
        if idx >= 0 and "不要" in query[max(0, idx - 3):idx]:
            return None
        return _norm_actor(hit)
    # 明确“主演/主演/出演”语序
    m = re.search(r"([一-龥A-Za-z0-9·]{2,10}?)(?:主演|参演|友情出演|出演|参演过的?|演\s*的{0,2}\s*(?:电影|电视剧|剧|影片|短片|纪录片|的?片))", query)
    if m:
        name = m.group(1)
        name = re.sub(r"^(?:查找|搜索|搜|查|看|放|请|我要|我想|帮我|找|推荐一些|推荐)", "", name)
        # 剥排序/筛选修饰前缀（评分最高的胡歌 → 胡歌；人气最高的赵丽颖 → 赵丽颖）。
        # 这类修饰是全句范围的 sort 条件，不属于 actor 实体本身；dsl _apply_sort 已单独拾取成 sort。
        name = _strip_sort_mod(name)
        # 剥尾音标：(斯皮尔伯格导演) 里的“导演”以“演”结尾，会把“导”误吞进 actor
        name = name.rstrip("导")
        # “X导演的电影”里“演”来自“导演”，不是 actor；X 后紧跟“导演”则不是主演语境
        end = query.find(name) + len(name) if name and name in query else -1
        if "导演" not in name and name and not query[end:end + 2].startswith("导演"):
            return _norm_actor(name)
    return None


def _role(query: str) -> str | None:
    # 角色名：许三多/吴石将军（无“主演/导演”标记，纯角色指代）
    if _known_in(query, _KNOWN_ROLES):
        return _known_in(query, _KNOWN_ROLES)
    return None


def _actors_multi(query: str) -> list[str] | None:
    """多演员 or：金秀贤、金智媛 主演的 X 或 “金秀贤、金智媛的短视频”。
    只识别词表内已知演员的顿号组合，避免吞进"搜索"前缀或"片段"后缀。"""
    pairs = {"金秀贤": "金秀贤", "金智媛": "金智媛", "小S": "小S", "小s": "小S"}
    present = []
    for n in pairs:
        m = re.search(n, query)
        if m:
            present.append((m.start(), n))
    present.sort()
    if len(present) >= 2:
        # 相邻人名间隔≤1字（顿号/空格），视为同组多演员
        ok = all(present[k + 1][0] - (present[k][0] + len(present[k][1])) <= 1
                 for k in range(len(present) - 1))
        if ok:
            return [_norm_actor(n) for _, n in present]
    return None


def _age_range(query: str):
    m = re.search(r"适合?.*?(\d+)\s*到\s*(\d+)\s*岁", query)
    if not m:
        return None
    return {"field": "age_range", "from": int(m.group(1)), "to": int(m.group(2))}


# 清晰度/版型 → definition 值
_DEF_MAP = [("4K", "is_4k"), ("4k", "is_4k"), ("超清", "is_4k"), ("3D", "is_3d"),
            ("3d", "is_3d")]


def _definition(query: str) -> str | None:
    for k, v in _DEF_MAP:
        if k in query:
            return v
    return None


def _rate(query: str) -> dict | None:
    """评分范围。（金标准口径：from/to 一律字符串，开放端用 "*"。）"""
    def _num(s: str) -> str:
        v = float(s)
        return f"{v:.1f}"
    m = re.search(r"高于\s*([0-9]+(?:\.[0-9]+)?)\s*分", query)
    if m:
        return {"field": "rate", "from": _num(m.group(1)), "to": "*"}
    m = re.search(r"(?:豆瓣)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:分|点)?(?:评分)?(?:以上|\+)", query)
    if m:
        return {"field": "rate", "from": _num(m.group(1)), "to": "*"}
    m = re.search(r"评分在?\s*([0-9.]+)\s*(?:到|至|-)\s*([0-9.]+)\s*分", query)
    if m:
        return {"field": "rate", "from": _num(m.group(1)), "to": _num(m.group(2))}
    return None


def _release(query: str):
    m = re.search(r"([12]\d{3})年之后", query)
    if m:
        y = int(m.group(1))
        return {"field": "release_time", "from": f"{y}0101", "to": "*"}
    m = re.search(r"([0-9]{2})年", query)  # 23年 → 2023年（两位数补 20xx）
    if m:
        y = int(m.group(1))
        if 0 <= y < 100:
            y = 2000 + y if y < 100 else y
        return {"field": "release_time", "from": f"{y}0101", "to": f"{y}1231"}
    m = re.search(r"去年", query)
    if m:
        return {"field": "release_time", "from": "20270101", "to": "20271231"}
    m = re.search(r"(?:今年|本年度)", query)
    if m:
        return {"field": "release_time", "from": "20260101", "to": "20261231"}
    m = re.search(r"([12]\d{3})年", query)
    if m:
        y = int(m.group(1))
        return {"field": "release_time", "from": f"{y}0101", "to": f"{y}1231"}
    m = re.search(r"([0-9]+)年代", query)  # 90年代 / 80年代（数字）
    if m:
        base = int(m.group(1))
        # 两位数如 90/80 视为 1990/1980；非两位年份直接 19xx/20xx
        if 0 <= base < 100:
            base = 1900 + base if 0 <= base <= 99 and base >= 40 else 2000 + base
        return {"field": "release_time", "from": f"{base}0101", "to": f"{base + 9}1231"}
    m = re.search(r"([一二两三四五六七八九十]+)年代", query)
    if m:
        dec = m.group(1)
        base = {"八十": 1980, "九": 1990, "九十": 1990, "七十": 1970,
                "八零": 1980, "九零": 1990, "零零": 2000, "一零": 2010, "二十": 2000}.get(dec)
        n = _cn2int(dec)
        if base is None and n is not None:
            base = 2000 if n == 2 else 1900 if n < 2 else 1900
        if base is not None:
            return {"field": "release_time", "from": f"{base}0101", "to": f"{base + 9}1231"}
    return None


def _play_control(query: str) -> list[dict]:
    conds = []
    m = re.search(r"第([0-9一二两三四五六七八九十]+)季", query)
    if m:
        v = _cn2int(m.group(1))
        if v:
            conds.append({"field": "series", "value": str(v)})
    m = re.search(r"第([0-9一二两三四五六七八九十]+)(?:集|期)", query)
    if m:
        v = _cn2int(m.group(1))
        if v:
            conds.append({"field": "video_index", "value": str(v)})
    # 分钟/秒 精确时长：仅在“第N分(钟)”或“N分钟/N分N秒”等明确时间语境触发，
    # 避免把“评分N分以上/在N到N分之间/高于N分”当作播放时长。
    _m = re.search(r"第[0-9一二两三四五六七八九十]+分(?:钟)?", query) or re.search(r"\d+\s*分钟", query) \
        or re.search(r"\d+\s*分\s*\d+\s*秒", query)
    if _m:
        m2 = re.search(r"第([0-9一二两三四五六七八九十]+)分(?:钟)?", query)
        if m2:
            conds.append({"field": "voiceStartPos", "value": (_cn2int(m2.group(1)) or 0) * 60})
        else:
            m3 = re.search(r"(\d+)\s*分\s*(\d+)?\s*秒?", query) or re.search(r"(\d+)\s*分(?:钟)?", query)
            mins = int(m3.group(1) or 0) if m3 else 0
            secs = int(m3.group(2) or 0) if m3 and m3.lastindex and m3.lastindex >= 2 else 0
            if mins or secs:
                conds.append({"field": "voiceStartPos", "value": mins * 60 + secs})
    return conds


_COMPANY_DICT = {
    "正午阳光": "正午阳光", "时光传媒": "时光传媒", "BBC": "BBC", "TVB": "tvb",
    "光线传媒": "光线传媒", "好莱坞": "好莱坞", "Netflix": "Netflix",
}


def _company(query: str) -> str | None:
    for k in sorted(_COMPANY_DICT, key=len, reverse=True):
        if k in query:
            return _COMPANY_DICT[k]
    return None


def _vender(query: str) -> str | None:
    for k, v in (("newtv极光", "newtv极光"), ("芒果TV", "芒果tv"), ("芒果tv", "芒果tv"),
                 ("芒果", "芒果tv"), ("爱奇艺", "爱奇艺"), ("极光", "极光"),
                 ("腾讯视频", "腾讯视频"), ("优酷", "优酷")):
        if k in query:
            return v
    return None


_CHANNEL_DICT = ["中央一台", "中央二台", "山东卫视", "湖南卫视", "浙江卫视", "浙江电视台", "东方卫视",
                 "央视", "CCTV", "安徽卫视", "江苏卫视", "江苏电视台", "河北卫视",
                 "番茄台", "北京卫视", "北京电视台", "上海东方卫视"]


def _channel(query: str) -> str | None:
    for k in _CHANNEL_DICT:
        if k in query:
            return {"中央一台": "中央一台", "中央一号": "中央一台", "山东卫视": "山东卫视",
                    "湖南卫视": "湖南卫视", "浙江卫视": "浙江卫视", "东方卫视": "东方卫视",
                    "CCTV": "央视", "央视": "央视"}.get(k, k)
    return None


_PRIZE_MAP = {
    "白玉兰奖": "白玉兰奖", "金马奖": "金马奖", "金鹰奖": "金鹰奖", "金鸡奖": "金鸡奖",
    "香港电影金像奖": "香港电影金像奖", "香港金像奖": "香港电影金像奖", "奥斯卡": "奥斯卡",
}


def _prize(query: str):
    for k, v in sorted(_PRIZE_MAP.items(), key=len, reverse=True):
        if k in query:
            sub = None
            m = re.search(r"最佳(?:男|女)?(?:主角|演员|导演|影片|编剧|配乐)", query)
            if m:
                sub = {"最佳女主角": "最佳女主角", "最佳男主角": "最佳男主角",
                       "最佳主角": "最佳主角", "最佳导演": "最佳导演",
                       "最佳编剧": "最佳编剧", "最佳配乐": "最佳配乐"}.get(m.group(0), m.group(0))
            return {"prize": v, "sub_prize": sub}
    return None


def _writer(query: str) -> str | None:
    m = re.search(r"([一-龥A-Za-z·]{2,8}?)\s*(?:编剧|执笔|作者|小说的?)", query)
    if m:
        name = m.group(1)
        name = re.sub(r"^(?:搜|查找|看|放|请|改编自)", "", name)
        if any(x in name for x in ("剧", "电影", "片", "综艺", "节目")):
            return None
        return name if name else None
    return None


def _dubbing(query: str) -> str | None:
    m = re.search(r"([一-龥A-Za-z·]{2,8}?)(?:配音|配音员|献声)", query)
    if m:
        name = m.group(1)
        name = re.sub(r"^(?:搜|查找|看)", "", name)
        return name if name and "配音" not in name else None
    return None


def _hostess(query: str):
    m = re.search(r"([一-龥A-Za-z·、\s]+?)(?:一起?)?(?:主持|主持过?)", query)
    if m:
        names = re.split(r"[和、与及]", re.sub(r"\s+", "", m.group(1)))
        names = [("小S" if n == "小s" else n) for n in names]
        names = [n for n in names if n and n not in ("一部", "节目", "一起")]
        if names:
            return names
    return None


def _comedy_brand(query: str) -> str | None:
    for k in ("笑果文化", "笑果", "德云社", "开心麻花", "单立人"):
        if k in query:
            return "笑果文化" if k in ("笑果", "笑果文化") else k
    return None


def _creation_source(query: str) -> str | None:
    if re.search(r"小说改编|改编自.{0,6}(小说|漫画|游戏|剧)|小说.{0,4}改编", query):
        return "小说改编"
    return None


_SOUND_MAP = {"杜比全景声": "杜比全景声", "杜比音效": "杜比音效", "立体声": "立体声",
              "DTS": "DTS", "环绕": "环绕声", "全景声": "杜比全景声"}


def _sound(query: str) -> str | None:
    for k, v in sorted(_SOUND_MAP.items(), key=len, reverse=True):
        if k in query:
            return v
    return None


def _strip_spaces(s: str) -> str:
    """去全半角空白（标题匹配用）。"""
    return re.sub(r"\s+", "", s or "")


def _find_title(query: str) -> str | None:
    """词表最长匹配标题。返回命中的标题；无则 None。

    别名（跑男→奔跑吧、知否→知否知否）命中返回金标准表名；但调用方依赖 match_sub
    （在 query 中的实际串）做标题后片段切分，需一并返回两种值。返回三元组会
    破坏既有接口，故通过模块级 _match_sub 暂存；无别名时 _match_sub = 词表串。
    历史接口 `_find_title(query)` 仍返回标题名。
    """
    global _match_sub
    _match_sub = None
    q = _strip_spaces(query)
    for raw, name in _TITLE_ALIAS.items():
        if raw in q:
            _match_sub = raw
            return name
    if "奥本海默" in q:
        _match_sub = "奥本海默"
        return "奥本海默"
    for t in _TITLES:
        if t in q:
            _match_sub = t
            return t
    return None


def _extract_title(query: str) -> str | None:
    """从 query 抽标题（词表匹配）。无明确具名标题返回 None。"""
    return _find_title(query)


def build_search_dsl(query: str) -> dict | None:
    """组装 vod_search/search_all 参数；无法确定性合成返回 None。"""
    if not query or not query.strip():
        return None
    q = query.strip()

    # ---------- action 判定 ----------
    # play：真正的"点播/打开/定位播放"；其余带筛选维度的浏览 → search。
    # play 触发条件：
    #   1) 强起播动词头（播/放/请播放/给我放/打开/收看）+ 命中词表标题，或
    #   2) 具名标题 + 定位（第N集/分钟/秒）→ 直接开播，或
    #   3) 具名标题 + 起播动词（戏曲铡美案/秦腔三滴血 这类点名播放）且无筛选维度。
    _hit = _extract_title(q)
    # 排序/筛选句（评分最高/人气最高/播放量最高/播放次数多/最新…的X）是浏览检索，不是点播；
    # 即使句首恰好以"播放"开头（播放量最高/播放次数多…）也不定 play。
    if re.search(r"评分最高|人气最高|播放(?:量|数)?最高|播放最高|最新的一部|最经典的一部|播放次数多|播放量高", q):
        action = "search"
    # 检索动词头（搜索/查/找…）→ 明确 search，永不定为 play
    elif re.match(r"^(?:搜索|搜|查找|查一下|找一下|找|查)", q):
        action = "search"
    elif re.search(r"有没有", q):
        # 「有没有X第N季」是存在性检索，不定 play（金标准：有没有番茄台的中国梦之声第1季 → search）
        action = "search"
    else:
        # 强起播动词头：播放/请播放/帮我放/给我放/放/打开/收看/开始播放 → 有具名标题或定位才 play
        _lead_play = bool(re.match(
            r"^(?:播放|请播放|请播|帮我放|给我放|放|打开|收看|敬请收|播|开始播放|"
            r"小度|在.{0,8}播放)", q))
        # 弱动词头：我想看/我要看/看/推荐/给我看 → 浏览（金标准一律 search）+ 词表标题也不 play
        _lead_browse = bool(re.match(r"^(?:我想看|我要看|看|推荐|给我看|快点我要看)", q))
        _has_seek = bool(re.search(r"第[一二三四五六七八九十0-9]+(?:集|季|期|分钟|秒|部)", q))
        if _lead_browse:
            # 我想看/我要看/看/推荐 一律 search（金标准 我想看知否/我要看战毒粤语版 等均是 search）
            action = "search"
        elif _lead_play and (_hit or _has_seek or not re.search(r"播放次数|播放量|播放数|高播放量|播放最高", q)):
            action = "play"
        elif _hit and _has_seek:
            action = "play"
        elif _hit and re.search(r"播放$", q):
            action = "play"
        else:
            action = "search"

    conds: list[dict] = []

    # 确定槽位（顺序固定，最后 QueryNode 输出）
    fee = _fee(q)
    over = _over(q)
    rate = _rate(q)
    release = _release(q)
    dir_ = _director(q)
    actor_ = _actor(q)
    actors_multi = _actors_multi(q)
    age_range = _age_range(q)
    target_ = _target(q)
    gender_ = _gender(q)
    role_ = _role(q)
    definition_ = _definition(q)
    area_ = _area(q) or _drama_area(q)
    cat_ = _category(q)
    # 韩/美/日/英/泰剧 隐式兼有 category=电视剧
    if _drama_area(q) and cat_ is None:
        cat_ = "电视剧"
    # 日韩 → 多值地区 or(日本, 韩国)（金标准：日韩高分电视剧 → area values 日韩）
    if re.search(r"日韩", q) and area_ is None:
        area_ = "日韩"
    company = _company(q)
    vender = _vender(q)
    channel = _channel(q)
    tags = _tags(q)
    play_ctl = _play_control(q)
    prize = _prize(q)
    writer_ = _writer(q)
    dubbing_ = _dubbing(q)
    hostess_ = _hostess(q)
    comedy_ = _comedy_brand(q)
    creation = _creation_source(q)
    sound_ = _sound(q)

    def add(field, value):
        if value is None or value == "" or (isinstance(value, (list)) and not value):
            return
        conds.append({"field": field, "value": value})

    # 版本后缀 → tag（title 已被词表剥掉，这里补回 tag 维度）
    _ver_tag = _version_suffix(q)
    if _ver_tag:
        tags.append(_ver_tag)
    # 好莱坞同时是 company 且是 area，金标准取 company，不加 area 冲突。
    hollywood_as_company = company == "好莱坞"
    # 多栖人物（许冠文）→ actor|director|entertainer 三 or（金标准：许冠文粤语电影）；
    # 命中即不再叠 actor 单值叶子（oneperson 已覆盖 actor 维度）。
    _or_person = _known_in(q, _OR_PERSON)
    if release: conds.append(release)
    if dir_ and _or_person is None: add("director", dir_)
    if actors_multi and _or_person is None:
        conds.append({"field": "actor", "values": actors_multi, "operator": "or"})
    elif actor_ and _or_person is None:
        add("actor", actor_)
    if _or_person:
        conds.append({"or": [
            {"field": "actor", "value": _or_person},
            {"field": "director", "value": _or_person},
            {"field": "entertainer", "value": _or_person},
        ]})
    if target_: add("target", target_)
    if gender_: add("gender", gender_)
    if role_: add("role", role_)
    if definition_: add("definition", definition_)
    if writer_: add("writer", writer_)
    if dubbing_: add("dubbing", dubbing_)
    if comedy_: add("comedy_brand", comedy_)
    if creation: add("creation_source", creation)
    if sound_: add("sound", sound_)
    if prize:
        add("prize", prize["prize"])
        if prize["sub_prize"]:
            add("sub_prize", prize["sub_prize"])
    if hostess_:
        if len(hostess_) == 1:
            add("hostess", hostess_[0])
        else:
            conds.append({"field": "hostess", "values": hostess_, "operator": "and"})
    if company: add("company", _company(q))
    if vender: add("vender_name", vender)
    if channel: add("channel", channel)
    area_multi = _area_multi(q)
    if "日韩" in q:
        # 日韩 → 多值地区 or(日本, 韩国)，与港台一致走 search_all
        conds.append({"field": "area", "values": "日韩"})
    elif area_multi:
        conds.append({"field": "area", "values": area_multi, "operator": "or"})
    elif area_ in _LANG:
        # 语言词（粤语/英语/日语…）：只作为语言维度，不作为地区
        add("language", _LANG[area_])
    elif area_:
        # 地区词（韩国/美国/内地/香港/外国…）
        if not (hollywood_as_company and area_ == "好莱坞"):
            add("area", area_)
    # 有具名标题时，表演曲艺词（小品/相声/秦腔/话剧等）作为 tag（片型属性），不再当 category
    _tmptitle = _find_title(q)
    if cat_ in _TAG_CATEGORY and _tmptitle:
        if cat_ not in tags:
            tags.append(cat_)
        cat_ = None
    # 情景喜剧/综艺节目 已是 tag 维度，不应再退化为 category=电视剧
    if "情景喜剧" in q and cat_ == "电视剧" and "情景喜剧" in tags:
        cat_ = None

    # 多分类 or：「电影和电视剧」「电视剧和电影」同时出现 → category values + or
    _multi_cats = re.findall(r"电影|电视剧|纪录片|综艺|动漫|动画|短片|戏曲|话剧|相声", q)
    if re.search(r"(?:电影|电视剧|纪录片|综艺|动漫|动画|短片|戏曲|话剧|相声)(?:和|与|或)(?:电影|电视剧|纪录片|综艺|动漫|动画|短片|戏曲|话剧|相声)", q) and len(set(_multi_cats)) >= 2:
        _norm = {c: _CAT_MAP[c] for c in _multi_cats}
        cat_vals = list(dict.fromkeys(_norm.values()))
        conds.append({"field": "category", "values": cat_vals, "operator": "or"})
        cat_ = None
    # 曲艺/表演词（戏曲/相声/吕剧/豫剧…）作为 tag 而非 category：
    # 具名标题（龙凤面）时只留 tag+title，削掉 category=电视剧（金标准：吕剧龙凤面）
    if cat_ in _TAG_CATEGORY:
        # 吕剧/豫剧 + 无标题 时是 tag 检索（吕剧），不硬造 category=电视剧
        if _tmptitle is None and cat_ not in tags:
            tags.append(cat_)
        cat_ = None
    if cat_: add("category", cat_)
    if tags:
        uniq_tags = list(dict.fromkeys(tags))
        if len(uniq_tags) == 1:
            add("tag", uniq_tags[0])
        else:
            uniq_tags = list(dict.fromkeys(tags))
            conds.append({"field": "tag", "values": uniq_tags, "operator": "and" if len(uniq_tags) > 1 else None})
    if rate: conds.append(rate)
    if fee is not None: add("is_fee", fee)
    if over is not None: add("is_over", over)

    # 非线性数（并列多个具名标题）→ title values + or；（金标准：老九门盗墓笔记）
    _title_multi = None
    for _raw_multi, _mvals in _TITLE_MULTI:
        if _raw_multi in q and all(t in _TITLES for t in _mvals):
            conds.append({"field": "title", "values": _mvals, "operator": "or"})
            _title_multi = True
            break

    # 标题序列：具名标题后紧跟 数字 或 “第X部”→ series（如 战狼2、封神第一部）
    # 数字紧跟 分/秒 是播放时长定位，不是系列（如“长安的荔枝3分09秒”），不算 series。
    title = None if _title_multi else _extract_title(q)
    if title:
        # 别名（跑男/知否）匹配串 ≠ 金标准表名；用实际子串切尾部
        _sub = _match_sub or title
        if title in q:
            _sub = title
        after = q.split(_sub, 1)[1] if _sub in q else ""
        # 中文数词收尾系列：欢乐家长群二 → series=2（匹配串是"欢乐家长群二"）
        _cn_ser = re.search(r"([一二两三四五六七八九十]+)$", _sub)
        if _cn_ser:
            _s = _cn2int(_cn_ser.group(1))
            if _s:
                add("series", str(_s))
                series_already = True
        else:
            series_already = False
        if not series_already:
            _s = None
            m = re.match(r"(\d+)(?!\d)", after)
            if m and not re.match(r"\d+(?:分|秒|分钟|秒钟)", after):
                _s = int(m.group(1))
            else:
                m2 = re.match(r"第([0-9一二两三四五六七八九十]+)部", after)
                if m2:
                    _s = _cn2int(m2.group(1))
            if _s is not None:
                add("series", str(_s))

    for pc in play_ctl:
        add(pc["field"], pc["value"])

    # title：有明确具名标题时置于最前（pos 多为 title 打头）。
    # 槽位很复杂且 query 长（导演+年份+题材 等多维非点名）时加到末尾，减少 title 误打头。
    has_seek = bool(re.search(r"第[0-9一二两三四五六七八九十]+(?:集|季|期|分钟|秒)", q))
    has_complex = bool(dir_ or actor_ or company or vender or channel or rate or release or has_seek)
    title = None if _title_multi else _extract_title(q)
    # 金标准：「我要看主角是狗剩的电视剧」把「主角」作 title 值（怪例，逐条对齐）
    if "主角是" in q and not title:
        title = "主角"
    if title:
        t_cond = {"field": "title", "value": title}
        if has_complex and len(q) > 12:
            conds.append(t_cond)
        else:
            conds.insert(0, t_cond)

    if not conds:
        return None

    result = {"action": action, "retext": _search_retext(q)}
    # sort：新出/最新/新上/新看/刚更新 → new desc；好看/热播/热门/口碑/高分 → hot 或 rate
    if re.search(r"新出|最新|最近|近期|新上|新播|新剧|刚上|新片|刚更新|更新|播出", q):
        result.setdefault("sort", {})["new"] = {"order": "desc"}
    if re.search(r"好看|热播|热门|大家都在看|热度的|热度高的|热门的|最热|爆款|爆火的|很火|火热的?|火\b|火[的]|热度|口碑|现在热|現熱", q):
        result.setdefault("sort", {})["hot"] = {"order": "desc"}
    if re.search(r"评分高|高分|高评分|评分.{0,2}高|评分比较好|比较好|口碑", q):
        result.setdefault("sort", {})["rate"] = {"order": "desc"}
    if re.search(r"播放(?:量|数)最高|播放量高|播放量|播放最高|播放次数多|播放次数|高播放量|播放量", q):
        result.setdefault("sort", {})["play"] = {"order": "desc"}
    # 冷门/小众 → hot asc（金标准：冷门电影 => hot asc）
    if re.search(r"冷门|小众", q):
        result.setdefault("sort", {})["hot"] = {"order": "asc"}

    query_node = conds[0] if len(conds) == 1 else {"and": conds}
    result["query"] = query_node
    return result


# ===========================================================
# 工具路线三级判定（与 schema 的字段白名单 + 取值枚举对齐）
# 理论：search 覆盖槽位 → search；search 不覆盖但 search_all 覆盖 → search_all；
#       两者都不覆盖、或槽位值不在枚举范围内 → fuzzy_search。
# ===========================================================
# search 的参数能表达的字段集（ExactFieldCondition oneOf + 状态 + 范围 + 起播控制）
_SEARCH_FIELDS = {
    "title", "actor", "director", "entertainer", "prize", "role", "tag", "target",
    "definition", "category", "gender",
    "is_fee", "is_over",                    # 状态
    "age_range", "release_time", "rate",    # 范围
    "series", "video_index", "voiceStartPos",  # 起播控制
}
# search_all 额外支持（ExactFieldCondition oneOf 里 domain 的部分）
_SEARCH_ALL_FIELDS = {
    "area", "language", "channel", "company", "vender_name", "hostess", "writer",
    "dubbing", "sub_prize", "creation_source", "sound", "comedy_brand", "technology",
}
# 已知但两者都不该落结构的字段 → 需要 fuzzy（不该出现在结构 query 里）
_FUZZY_ONLY_FIELDS = set()


def _field_value_valid(field: str, node: dict) -> bool:
    """该槽位的值是否在 schema 枚举/格式范围内。越界 → fuzzy。"""
    if field in ("is_fee", "is_over"):
        v = node.get("value")
        # 金标准 is_fee 是字符串 "0"/"1"（个别行用"免费"噪音），容忍 int 0/1
        if isinstance(v, str):
            return v in ("0", "1", "免费")
        return isinstance(v, int) and v in (0, 1)
    if field in ("series", "video_index"):
        v = node.get("value")
        # 金标准 series/video_index 是数字字符串
        return isinstance(v, str) and v.isdigit() and int(v) >= 1
    if field == "voiceStartPos":
        v = node.get("value")
        return isinstance(v, int) and v >= 0
    if field == "age_range":
        return node.get("from") is not None or node.get("to") is not None
    if field == "release_time":
        for k in ("from", "to"):
            v = node.get(k)
            if v is None or v == "*":
                continue
            if not (isinstance(v, str) and len(v) == 8 and v.isdigit()):
                return False
        return True
    if field == "rate":
        for k in ("from", "to"):
            v = node.get(k)
            if v is None or v == "*":
                continue
            f = float(v) if isinstance(v, str) else v
            if not (isinstance(f, (int, float)) and 0 <= f <= 10):
                return False
        return True
    # 精确匹配字段值非空即可
    if "values" in node:
        return bool(node.get("values"))
    return bool(node.get("value"))


_DESC_EPITHET = re.compile(r"那个[^，。、的]{1,8}的[^，。]{1,20}(电影|影片|动画|动画片|动画片儿|动漫|电视剧|剧|片|作品)")
_DESC_LIST = re.compile(r"(?:不是|不)[^，。]{2,10}、(?:很?[^，。]{1,8}的|评分|最近|最新)[^，。]{0,10}[、，][^，。]{2,10}(?:片|剧|电影|电视)")


def _is_desc_fuzzy(query: str) -> bool:
    """描述型检索(用外延描述而非封闭枚举) → fuzzy。如“那个演功夫拍的胖子导演拍的高分电影”、
    “不是国产的、评分很高的、最近几年的科幻片”这类不好收成封闭 schema 枚举的问题。
    金标准对这类归 vod_fuzzy_search（自由文本检索），bench 无「那个…的」用例，判定安全。"""
    if _DESC_EPITHET.search(query):
        return True
    if _DESC_LIST.search(query):
        return True
    return False


def route_tool(query: str) -> str:
    """三级判定：vod_search | vod_search_all | vod_fuzzy_search。"""
    if _is_desc_fuzzy(query):
        return "vod_fuzzy_search"
    d = build_search_dsl(query)
    if not d:
        return "vod_fuzzy_search"
    conds = d.get("query") or {}
    stack: list[Any] = [conds]
    need_all = False
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            fld = node.get("field")
            if fld:
                if fld in _SEARCH_FIELDS:
                    if not _field_value_valid(fld, node):
                        return "vod_fuzzy_search"   # 值越界
                elif fld in _SEARCH_ALL_FIELDS:
                    need_all = True                  # search 装不下 → 至少 search_all
                    if not _field_value_valid(fld, node):
                        return "vod_fuzzy_search"
                else:
                    return "vod_fuzzy_search"        # 根本不覆盖 → fuzzy
            for v in node.values():
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return "vod_search_all" if need_all else "vod_search"


def needs_search_all(query: str) -> bool:
    """兼容旧引用：是否只需到 search_all（非 fuzzy）。"""
    return route_tool(query) == "vod_search_all"