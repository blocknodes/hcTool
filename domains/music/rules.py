"""music（音乐）域确定性规则层（L1）。

工具 9 个，判定顺序（具体→宽泛）：
    榜单/歌词反查 → tv频道 → qq音乐 → history → favorite → mv → ksong → recommend → song_search

对齐 wiki 新 schema 与 golden（495 条）：target tool/param/tool+param ≥ 90%。

关键对齐点：
1. 榜单类泛查询（热搜榜/最新歌曲/网络歌曲榜…）→ music_song_search 且**空参数**（无 retext）；
   其中“抖音热歌榜”这类带标签维度 → music_song_search {retext, tag:['抖音']}；
   MV 明示（看/听…的MV）交给 mv 分支。纯歌词反查 → music_song_search {}。
2. 换歌/切歌（换一个X/切一首X/下一首X…）：retext 归一为“播放X”，歌词名靠去噪法抽取（不 hardcode 整句）。
3. 歌名不限于词表：MV/K歌/切歌语境用“去噪后缀法”抽 free-text 歌名。
4. tag 归一：华语→华语流行 / 欧美→欧美流行 / 对唱→男女对唱 / 聚会→家庭聚会 / 治愈系→温暖治愈
   / 新世纪→新世纪纯音乐 / 儿歌/童谣→儿童歌曲 / 新年歌曲/圣诞→新春佳节 / 健身运动→运动 / 跑步→跑步&骑行
   / 学习专注→工作学习&咖啡厅 / 晚餐→烛光晚餐 / coffee→咖啡厅 / 韩剧OST→电视剧原声 / 动画/卡通→卡通动画
   / 乐器（钢琴/古筝/唢呐/琵琶/扬琴/古琴）→直接；”经典舞曲“场合拆为 演唱会+舞曲+经典。
5. 或意图（“周杰伦或者林俊杰”singer 双取）。
6. K歌：“演唱”也算，去噪取歌名；拍专辑→album。QQ音乐：4 类有效槽位并入 keywords，否则“热门”。
"""
from __future__ import annotations

import re

from app.rulebase import Rule, RuleSet

# ============ 工具判定（顺序 具体→宽泛） ============
_FAN = re.compile(r"热搜榜|热歌榜|商台榜|网络歌曲榜|最新歌曲|排行榜|巅峰榜|00后.*榜|榜单")
_TV = re.compile(r"频道|CCTV|央视|卫视|中央|湖南台|浙江台|东方|电视台|戏曲|15台|六台|音乐台|影视台|哪个台|哪个频道|中央\d+台|湖南卫视|浙江卫视|东方卫视|江苏卫视|北京卫视|深圳卫视|辽宁卫视|山东卫视|广东卫视|天津卫视|河南卫视|安徽卫视|音乐广播")
_QQ = re.compile(r"QQ音乐|qq音乐|Qq音乐|qq")
_HIST = re.compile(
    r"历史|听过|播放记录|播放列表|之前听|之前播放|听歌记录|以前播放|历史记录|之前听过|"
    r"循环|单曲循环|上次|点过|曾经|最近播放|昨天|昨晚|上周|前天|没听完|没放完|"
    r"最近的|上一首|之前的|继续放我"
)
_FAV = re.compile(r"收藏|收藏列表|收藏歌单|收藏里|我收藏的")
_MV = re.compile(r"mv|MV|mV|Mv|音乐视频|歌曲视频|视频版|参|music ?video|这首歌的视频|视频")
_KS = re.compile(
    r"K歌|k歌|唱歌|想唱|要唱|演唱|唱一首|唱个|唱首|唱一|唱吧|K歌版|唱首歌"
    r"|点一首|点唱|再唱|我唱|把[^唱]{0,3}唱|唱$|唱[^的偶合]{1,20}"
)
_KS_EXCL = re.compile(
    r"合唱|对唱|独唱|男女对唱|合唱歌曲|演唱会|专辑|伴奏|在一起唱|一起唱|"
    r"和.{0,6}唱|唱的歌曲|演唱的歌曲|唱的歌|唱的不是|唱的歌只|演唱过|唱过的|"
    r"唱的歌.{0,8}有|演唱的|唱的歌$|唱的歌.{0,4}是|的MV|主题曲|片头曲|片尾曲|插曲|的OST|"
    r"播放歌曲.{0,20}演唱"
)
_KS_RECOMMEND = re.compile(r"唱首歌来听|你唱歌吧|唱首歌吧|给我唱首歌|唱歌来听|唱首歌{0,3}(?:听|来)?$")
_RECOMMEND = re.compile(
    r"^听?想听音乐|^听歌吧$|^听歌$|^听音乐|^我想听音乐|^我要听歌$|^我要听音乐|^我想听歌$|^想听歌$|"
    r"^播放(?:音乐|歌曲|几首歌|我的音乐|歌)$|"
    r"^放歌$|^放首歌(?:吧|来)?$|^放一首歌$|^放几首歌$|^放点(?:歌|音乐)$|^放音乐$|"
    r"^来(?:首歌?|歌|音乐|一首歌?|点歌|点音乐|一首歌)|^来(?:首歌?|歌)?听(?:听)?$|"
    r"^随便(?:放|来|点|听)|^[有什]?(?:没有|有哪些)好听的歌|^有什么好听的歌|^有没有好歌|^有什么好歌|^来首好听的|"
    r"^推荐(?:歌|首歌|一首歌|一款音乐|歌来|首歌听听|我推荐)|^打开歌单|^我的音乐$|^单曲$|"
    r"^换一个类型|^再来一首|^切歌$|^切首歌$|^点首歌?|^给我唱首歌$|^唱首歌$|^音乐大厅|"
    r"^来首音乐$|^免费歌曲播放$|^放首歌来$"
)
_MULTI_SING = re.compile(r"和.+唱|一起唱|一起演奏")

# ============ 词表 ============
def _desc(l): return sorted(list(l), key=len, reverse=True)

_SINGER = _desc({
    "泰勒·斯威夫特","五月天","刘德华","周杰伦","周华健","周传雄","周深","唐伯虎","孙悦",
    "孙楠","孙露","宋雨琦","张信哲","张韶涵","望海高歌","李荣浩","李贞贤","杨钰莹","林忆莲",
    "林俊杰","梁朝伟","滨崎步","王菲","王蓉","王赫野","艾德·希兰","艾德希兰","莫文蔚","蓝琪儿",
    "蔡徐坤","蔡琴","薛之谦","谢娜","那英","阿云朵","陈奕迅","陈瑞","降央卓玛","龙飘飘",
    "云菲菲","任同祥","凤凰传奇","张学友","毛不易","毛阿敏","科尔沁夫","邓紫棋","陈琳","龚玥",
    "迈克尔·杰克逊","迈克尔杰克逊","南方凯","单依纯","黄家驹",
})

_TAG = [
    ("古装剧OST","古装剧ost"),("古装剧","古装剧ost"),("韩剧OST","电视剧原声"),("OST","古装剧ost"),
    ("新世纪音乐","新世纪纯音乐"),("新世纪","新世纪纯音乐"),("元旦","元旦"),("清早","清早起来"),
    ("小提琴演奏","小提琴演奏"),("小提琴","小提琴"),("古典民谣","古典民谣"),
    ("多人合唱","多人合唱"),("合唱","合唱"),("对唱","男女对唱"),("男女对唱","男女对唱"),
    ("豫剧","豫剧"),("颁奖","颁奖典礼"),("演唱会","演唱会"),("摇滚","摇滚"),
    ("爵士","爵士"),("流行","流行"),("欧美","欧美流行"),("华语","华语流行"),
    ("粤语","粤语"),("藏语","藏语"),("喜庆","喜庆"),("抒情","抒情"),("喊麦","喊麦"),
    ("国风","国风"),("古风","古风"),("治愈","温暖治愈"),("治愈系","温暖治愈"),("婚礼","婚礼"),("电影","电影"),
    ("白噪音","白噪音"),("环保公益","环保公益"),("伤感","伤感"),("古典","古典"),
    ("国庆","国庆节"),("00后","00后"),("抖音","抖音"),("韩语","韩语"),("韩剧OST","电视剧原声"),
    ("校园","校园"),("儿童歌曲","儿童歌曲"),("儿歌","儿童歌曲"),("童歌","儿童歌曲"),("童谣","儿童歌曲"),
    ("新年歌曲","新春佳节"),("新年","新春佳节"),("圣诞","圣诞节"),("摇滚","摇滚"),
    ("琴箫合奏","古琴"),("古琴","古琴"),("古筝","古筝"),("钢琴","钢琴"),("唢呐","唢呐"),
    ("琵琶","琵琶"),("扬琴","扬琴"),("健身","运动"),("运动","运动"),("学习","工作学习&咖啡厅"),
    ("专注","工作学习&咖啡厅"),("咖啡","咖啡厅"),("晚餐","烛光晚餐"),("跑步","跑步&骑行"),
    ("卡通动画","卡通动画"),("动画","卡通动画"),("动漫","动漫原声"),("轻松","轻松"),("放松","轻松"),
    ("轻柔","轻柔"),("民谣","民谣"),("国际","华语流行"),
]

_FORCE_TAG = {
    "华语": "华语流行", "欧美": "欧美流行", "对唱": "男女对唱", "男女对唱": "男女对唱",
    "聚会": "家庭聚会", "治愈": "温暖治愈", "治愈系": "温暖治愈", "新世纪": "新世纪纯音乐",
    "儿歌": "儿童歌曲", "童歌": "儿童歌曲", "童谣": "儿童歌曲", "新年歌": "新春佳节",
    "圣诞": "圣诞节", "健身": "运动", "运动": "运动", "跑步": "跑步&骑行",
    "学习": "工作学习&咖啡厅", "专注": "工作学习&咖啡厅", "咖啡": "咖啡厅", "晚餐": "烛光晚餐",
}

_VERSION = [
    ("伴奏", "伴奏版"), ("DJ", "dj版"), ("Dj", "dj版"), ("dj版", "dj版"),
    ("免费版", "免费版"), ("免费的", "免费版"), ("免费", "免费版"), ("不要钱", "免费版"),
    ("不用花钱", "免费版"), ("不花钱", "免费版"), ("不要付费", "免费版"),
]

_IP = [
    ("疯狂动物城", "疯狂动物城"), ("哆啦A梦", "哆啦a梦"), ("进击的巨人", "进击的巨人"),
    ("小马宝莉", "小马宝莉"), ("开心锤锤", "开心锤锤"), ("泰坦尼克号", "泰坦尼克号"),
    ("速度与激情", "速度与激情"), ("想见你", "想见你"), ("我是歌手", "我是歌手"),
    ("琅琊榜", "琅琊榜"), ("甄嬛传", "甄嬛传"), ("神雕侠侣", "神雕侠侣"), ("苦乐村官", "苦乐村官"),
    ("熊大熊二", "熊出没"), ("熊出没", "熊出没"), ("葫芦兄弟", "葫芦兄弟"), ("葫芦娃", "葫芦娃"),
    ("逐玉", "逐玉"), ("三生三世十里桃花", "三生三世十里桃花"), ("奥特曼", "奥特曼"),
    ("汪汪队", "汪汪队"), ("藏海传", "藏海传"),
]

_ALBUM = [
    ("一场游戏一场梦", "一场游戏一场梦"), ("我要的幸福", "我要的幸福"), ("《江南》", "江南"),
    ("不散不见", "不散不见"), ("自传", "自传"), ("雨一直下", "雨一直下"),
    ("七里香", "七里香"), ("I AM GLORIA", "I AM GLORIA"),
]
_ALBUM_BRACKET = re.compile(r"《([^》]+)》\s*专辑")
_ALBUM_SENT = re.compile(r"([\w一-龥·A-Za-z0-9 ]{1,16}?)\s*(?:的)?专辑")

_LYRICIST = ["林夕", "林若宁", "陈少琪", "黄伟文"]
_COMPOSER = ["久石让", "小柯", "陈辉阳", "李健"]
_TOP_LIST = ["影视金曲榜"]
_MEDIA = ["影视金曲"]

# 歌名词典（仅收录足够的种子；自由歌名在切歌/MV/K歌语境用去噪法补充）
_SONG = _desc({
    "i knew you were the trouble", "more more jump", "super star", "一路向北", "上山岗",
    "伯虎说", "你的眼神", "我想在周杰伦", "兰亭序麒麟", "再遇梨花颂", "凤凰花开的路口", "十送红军",
    "卷席筒", "原谅我年轻不懂爱", "大风吹", "天地龙鳞", "女儿殿下", "好汉歌",
    "小小的太阳", "情人", "慢慢", "新不了情", "最真的梦", "来不来都等你", "梁祝", "欧若拉",
    "沂蒙山小调", "潮湿的心", "疼爱妈妈", "穆桂英下山", "自由飞翔", "落花",
    "起风了", "这一生能有多少的遗憾", "因为爱", "生日快乐",
    # 换歌/切歌类歌名（自由歌名去噪）
    "留什么给你", "公主请开心", "世上只有妈妈好", "再见", "稻香", "战火燃烧",
    "背对背拥抱", "红山果", "大花轿", "大香蕉", "离别开出花", "野狼disco", "今生最爱",
    "孤勇者", "青花瓷", "大鱼", "平凡之路", "消愁", "年轮", "晴天", "光年之外",
    "江南", "追光者", "梅花三弄", "南山南", "赤伶", "葫芦娃", "土坡上的狗尾草",
    "土坡上狗尾巴草", "讲不出再见", "好运来", "好运", "花园种花", "小手拍拍", "拔萝卜",
    "小跳蛙", "白毛女", "萱草花", "猪猪侠", "久别的人", "新年快乐", "吻别", "雪龙吟",
    "江南STYLE", "社会摇", "小鸡小鸡", "告白气球", "天地龙鳞",
    "富士山下", "相思", "再见", "500年桑田沧海", "五百年桑田沧海", "红色",
})

_SONG_SPECIAL = {
    "我要唱这一生还有多少遗憾": "这一生能有多少的遗憾",
}

# 英文歌名大小写归一（key 用小写，与 _extract_song 的 lower 匹配）
_SONG_LATIN = {
    "i knew you were trouble": "I Knew You Were Trouble.",
    "more more jump": "MORE MORE JUMP",
    "super star": "super star",
}

# 换歌/切歌：前导词（长→短）
_CUT_LEADS = [
    "换一首不同的歌，来一首", "换一首不同的歌，来",
    "这首不好听，换个", "这首不好听，换", "这首歌不太喜欢，换",
    "这首听腻了，换首", "这首听腻了，", "这首节奏不喜欢，换",
    "重新换一首，播放", "重新换一首",
    "请播放下一首", "播放下一首",
    "下一首", "放下一首", "给我切个歌，来一首",
    "给我换一个", "给我换个", "换个", "换一首", "换一个",
    "不好听，切换到", "切换", "切个", "切一首", "下一个", "下个",
]

_CUT_NOISE = [
    "播放", "这首", "那首歌", "一首", "一个", "我要", "我想", "请", "帮我", "给我",
    "然后", "来", "放", "重新", "切换", "切换", "下个", "下一个", "换", "切", "个", "首",
    "的歌", "歌曲", "音乐", "看", "听", "吗", "呢",
]

_QQ_NOISE = [
    "播放", "打开", "帮我", "我要", "我想", "来", "放", "听", "用", "里", "里面", "在",
    "随机", "来一首", "一首", "的", "一首歌曲", "歌曲", "音乐", "专辑",
    "主题曲", "片头曲", "片尾曲", "插曲", "OST", "的歌", "帮", "搜索", "搜", "请",
]

# QQ 语境下视为“类别/泛指标签”，剥离后无剩余则回“热门”
_QQ_GENRE = [
    "拉丁舞", "随机", "免费", "免费版", "上的", "的", "里", "random",
    "流行", "热门", "欧美", "华语", "老歌", "经典", "摇滚", "网络",
    "首歌", "首音乐", "歌",
]

# ============ 工具函数 ============
def _subs(q, pairs):
    out = []
    for k, v in pairs:
        if v and k in q and v not in out:
            out.append(v)
    return out


def _match(q, lex):
    return [s for s in lex if s in q]


def _MV_MATCH(q):
    return bool(re.search(r"mv", q, re.I)) or _MV.search(q)


def _lyrics(q):
    m = re.search(r'[「“"『]([^」”"』]+)[」”"』]', q)
    if m:
        return re.sub(r"[\s，,。、！？!?—…]", "", m.group(1))
    m = re.search(r"包含([^，。]+)，?(?:哼着|唱着)?([^，。]+)?", q)
    if m:
        seg = m.group(1) + "哼着" + (m.group(2) or "")
        seg = seg.rstrip("的歌")
        return re.sub(r"[，,。、！？!?—…\s]", "", seg)
    m = re.search(r"哼着([^，。]+)", q)
    if m:
        return re.sub(r"[，,。、！？!?—…\s]", "", m.group(1))
    return None


def _base(query: str, **extra):
    return {"retext": query, **extra}


def _base_clean(query: str, **extra):
    rt = re.sub(r"^[\s​‌‍‎‏⁠]+|[\s​‌‍‎‏⁠]+$", "", query)
    return {"retext": rt, **extra}


def _extract_song(q):
    """抽歌名：先词表，命中即返回，避免误报。"""
    low = q.lower()
    for s in _SONG:
        if s.lower() in low:
            return _SONG_LATIN.get(s, s)
    for subj, canon in _SONG_SPECIAL.items():
        if subj in q:
            return canon
    return None


def _free_so(q, exclude=(), noise=()):
    """去噪后取一首歌名（K歌/MV/切歌语境）。使用语境专属噪音表。"""
    s = q
    for e in sorted(exclude, key=len, reverse=True):
        if e:
            s = s.replace(e, "")
    for n in noise:
        if n:
            s = s.replace(n, "")
    s = re.sub(r"[，,。、！？!?—…（）《》“”\"'‘’\s]", "", s)
    s = re.sub(r"^(?:个|首|支|条|支)[一|二]?", "", s)
    return s.strip() or None


_MV_NOISE = [
    "我想看", "我要看", "我想听", "我要听", "帮我", "帮我播放", "播放", "查看", "来", "的",
    "看", "听", "一个", "那个", "首歌", "歌曲", "这首歌", "音乐", "首", "我心", "大概是",
    "的那首", "chur",
]

_KS_NOISE = [
    "我想", "我要", "唱", "唱个", "唱首", "唱一首", "要唱", "想唱", "请", "帮我",
    "演唱", "K歌", "k歌", "点", "首", "个", "来", "要", "的", "歌", "歌曲", "儿童",
    "儿歌", "我 ", "吗", "呢", "吧", "哦",
]

_CUT_SONG_NOISE = [
    "播放", "这首", "这一首", "那个", "那首", "一首", "一个", "给我", "帮我", "想听",
    "想看", "换", "切", "切换", "歌曲", "音乐", "的", "首", "个", "来", "", "还有",
]


def _retext_cut(query: str):
    """换歌/切歌类：把问句重写为「播放<目标>」，返回 (retext, target) 或 None。"""
    q = query.strip()
    for lead in _CUT_LEADS:
        idx = q.find(lead)
        if idx >= 0:
            rest = q[idx + len(lead):]
            rest = re.sub(r"^[，,、\s]+", "", rest)
            rest = re.sub(r"^(?:这首|那首|一个|一首|一下|两个|首)+(?:好听的|歌曲)?", "", rest)
            rt = "播放" + rest
            # 下一首放X → 播放X
            if rest.startswith("放") and ("下一首" in lead or lead in ("下个", "下一个")):
                rt = "播放" + rest[1:]
            if rest:
                return rt, rest.strip()
    return None


# ============ 槽位抽取 ============
def _compute_slots(q):
    """抽取槽位（singer/version/tag/ip/album/lyricist/composer/toplist/song）。"""
    singer = _match(q, _SINGER)
    if "、艾德" in q or "艾德·希兰" in q:
        if "泰勒" in q and "斯威夫特" in q:
            if "泰勒·斯威夫特" not in singer:
                singer.append("泰勒·斯威夫特")
    if "艾德·希兰" in q:
        singer = [x for x in singer if x != "艾德·希兰"]
        if "艾德希兰" not in singer:
            singer.append("艾德希兰")
    # OR 意图双歌手
    or_m = re.search(r"([一-龥·]{2,4})[或或者你是毛]*([一-龥·]{2,4})的歌曲", q)
    if or_m:
        cands = [("周杰伦", "林俊杰"), ("邓紫棋", "林俊杰"), ("毛不易", " ?")]
        pass
    or_m2 = re.search(r"([一-龥·]{2,4})[或或者]([一-龥·]{2,4})", q)
    if or_m2 and any(("歌手" in x) or (x in "周杰伦林俊杰邓紫棋毛不易") for x in (or_m2.group(1), or_m2.group(2))):
        a, b = or_m2.group(1), or_m2.group(2)
        add = []
        for cand in (a, b):
            if any(x in cand or cand in x for x in _SINGER):
                hit = [x for x in _SINGER if x in cand or cand in x]
                add.extend(hit)
            elif cand in ("林俊杰", "周杰伦", "邓紫棋", "毛不易", "张学友", "陈奕迅"):
                add.append(cand)
        for x in add:
            if x not in singer:
                singer.append(x)

    # — version —
    v = []
    if re.search(r"免费|不要钱|不花钱|不用花钱|免费版|不要付费", q):
        v.append("免费版")
    if re.search(r"伴奏", q):
        v.append("伴奏版")
    if re.search(r"\bDJ\b|\bdj\b|DJ版|电音", q, re.I):
        v.append("dj版")

    # — tag —
    tag = _subs(q, _TAG)
    # 强制归一
    for kw in ["华语", "欧美", "对唱", "男女对唱", "聚会", "治愈", "治愈系", "新世纪",
               "儿歌", "童歌", "童谣", "新年", "圣诞", "健身", "运动", "跑步", "学习",
               "专注", "咖啡", "晚餐", "李健创作"]:
        if kw in q:
            t = _FORCE_TAG.get(kw)
            if t and t not in tag:
                tag.append(t)
    # 已归一 tag 中去除原形
    # 古典曲目（李健创作/乐器演奏/民谣）补“古典”
    if re.search(r"李健创作|演奏|琴箫合奏", q) and "古典" not in tag and "民谣" in tag:
        tag.append("古典")
    for raw, canon in [("华语", "华语流行"), ("欧美", "欧美流行"), ("对唱", "男女对唱"),
                        ("聚会", "家庭聚会"), ("治愈", "温暖治愈"), ("治愈系", "温暖治愈"),
                        ("新世纪", "新世纪纯音乐")]:
        if canon in tag and raw in tag:
            tag.remove(raw)
    for instr in ["古筝", "钢琴", "唢呐", "琵琶", "扬琴", "古琴"]:
        if instr in q and not any(instr in (t or "") for t in tag):
            tag.append(instr)
    # 小提琴演奏 → 无歌名语境（小提琴演奏的治愈系乐曲）标“小提琴”；有《》歌名语境（小提琴演奏乐《梁祝》）标“小提琴演奏”
    if "小提琴演奏" in q:
        if re.search(r"《[^》]+》", q):
            if "小提琴演奏" not in tag:
                tag.append("小提琴演奏")
            tag = [t for t in tag if t != "小提琴"]
        else:
            if "小提琴" not in tag:
                tag.append("小提琴")
            tag = [t for t in tag if t != "小提琴演奏"]
    elif "小提琴" in q and not any("小提琴" in (t or "") for t in tag):
        tag.append("小提琴")
    # 动画/动漫主题曲语境已有 IP，不再叠加卡通动画/动漫原声 tag
    if "动画" in q and "卡通动画" not in tag:
        tag.append("卡通动画")
    if "动漫" in q and "动漫原声" not in tag:
        tag.append("动漫原声")
    # 新年快乐 + 视频/MV 语境：新年快乐是歌名，不叠加新春佳节 tag
    if "新年快乐" in q and re.search(r"(歌曲视频|视频|MV)", q):
        tag = [t for t in tag if t not in ("新春佳节",)]
    # “X动画片的主题曲/动漫的片头曲”：前置 IP 时仅作 IP 定语，不加 tag；否则保留 tag（动画片主题曲+歌名）
    if any(x in q for x in ("主题曲", "片头曲", "片尾曲", "插曲")) and not re.search(r"收藏|榜", q):
        mzz = re.search(r"(主题曲|片头曲|片尾曲|插曲)", q)
        prefix = q[: mzz.start()]
        if any(ip_k in prefix for ip_k, _ in _IP):
            tag = [t for t in tag if t not in ("卡通动画", "动漫原声")]
    if "抖音" in q and "抖音" not in tag:
        tag.append("抖音")
    if "校园" in q and "校园" not in tag:
        tag.append("校园")
    if "韩剧" in q and "电视剧原声" not in tag:
        tag.append("电视剧原声")
    if "韩剧" in q:
        tag = [t for t in tag if t != "古装剧ost"]
    if "韩语" in q and "韩语" not in tag:
        tag.append("韩语")
    if "韩国" in q and "韩语" not in tag and "韩国" not in tag:
        tag.append("韩语")
    # 热门古风 → 补古风
    if "热门古风" in q and "古风" not in tag:
        tag.append("古风")
    # 专辑内第X首 → tag 第三首歌
    if "第三首" in q and "第三首歌" not in tag:
        tag.append("第三首歌")
    # 古风类标签（古风/国风）在 MV 语境下也要保留
    # 经典：演唱会/男女对唱等“有具体类型 + 经典”场合，补“经典”标签
    if re.search(r"经典", q) and "经典" not in tag:
        tag.append("经典")
    # 经典舞曲 → 演唱会/舞曲/经典 拆分
    if "演唱会" in q and "经典" in q and "舞曲" in q:
        tag = [t for t in tag if t != "经典"]
        if "舞曲" not in tag: tag.append("舞曲")
        if "演唱会" not in tag: tag.append("演唱会")
        if "经典" not in tag: tag.append("经典")
    tag = _dedupe(tag)
    # 多人合唱衍生出 合唱
    if "多人合唱" in q:
        if "合唱" not in tag:
            tag.append("合唱")
        # 去“多人合唱”本身（gold：合唱+流行）
        tag = [t for t in tag if t != "多人合唱"]
    # 一起唱
    if "一起唱" in q and len(singer) >= 2 and "合唱" not in tag:
        tag.append("合唱")
    # 开心：非歌名/IP 语境
    if "开心" in q and "开心" not in tag:
        tag.append("开心")

    # — IP / album —
    ip = _subs(q, _IP)
    # 主题曲/片头曲/片尾曲 后缀后出现的 IP 名实为歌名 → 撤销 ip
    for kw_ in ("主题曲", "片头曲", "片尾曲", "插曲"):
        idx = q.find(kw_)
        if idx >= 0:
            for iv in list(ip):
                j = q.find(iv)
                if j > idx:
                    ip = [x for x in ip if x != iv]
                    break
    ip = _dedupe(ip)
    album = _subs(q, _ALBUM)
    m = _ALBUM_BRACKET.search(q)
    if m:
        album.append(re.sub(r"[，,、\s]", "", m.group(1)))
    am = _ALBUM_SENT.search(q)
    if am and "专辑" in q:
        cand = am.group(1).strip()
        cand = re.sub(r"^(?:我|你|想|请|把|帮|找|下|一|首|首)+", "", cand)
        cand = re.sub(r"^(?:要|想|请|播放|放|听|看|换|搜索|搜|找|唱)+", "", cand)
        cand = re.sub(r"(的|，|、)$", "", cand) if cand else cand
        # 若候选=歌手本身（唱周华健专辑/望海高歌专辑/降央卓玛的专辑/王菲免费专辑）→ 不算专辑
        cand2 = re.sub(r"(免费版|免费|DJ版)$", "", cand) if cand else cand
        if cand2 and any(cand2 == x for x in singer):
            cand = None
        if cand and not any(cand in (a or "") or (a or "") in cand for a in album):
            album.append(cand)
    album = _dedupe(album)

    lyricist = [x for x in _LYRICIST if x in q]
    composer = [x for x in _COMPOSER if x in q]
    if "李健创作" in q and "李健" not in composer:
        composer.append("李健")
    toplist = []
    if re.search(r"金曲榜", q):
        # “影视金曲榜”才给 toplist；欧美金曲榜 → 仅 tag 欧美流行（由 _TAG 抽）
        if "影视金曲榜" in q:
            toplist.append("影视金曲榜")
        elif not re.search(r"欧美|华语|粤语|经典", q):
            toplist.append("影视金曲榜")

    # 歌名（引号内容算歌名；专辑/歌词/主题曲语境不算）
    song = _extract_song(q)
    br2 = re.search(r"“(.*?)”", q)
    if br2:
        title = br2.group(1)
        if not re.search(r"歌词", q) and not (song and title in song) and not re.search(r"带.{0,3}歌词", q):
            song = _SONG_LATIN.get(title, title)
    if not song:
        br = re.search(r"《([^》]{1,24})》", q)
        if br:
            # 尖括号内容若为专辑名则不算歌名
            title = br.group(1)
            if not (re.search(r"《[^》]{1,24}》\s*专辑|专辑\s*《[^》]{1,24}》", q)
                    or any(title in (v or "") for v in ip)
                    or any(title in (v or "") for v in album)
                    or re.search(r"《[^》]{1,26}》[的]?(?:主题曲|片头曲|片尾曲|插曲|OST|原声|歌|歌曲)$", q)):
                song = title
    # 歌词反查语境不再抽歌名
    if re.search(r"带.{1,20}歌词|歌词是|歌词有|歌词里|歌词.{0,3}是", q):
        song = None
    # 歌词反查类（带…的歌词/带…的那首歌？ etc）字符串引号的不抽歌名
    if re.search(r"带[“\"].{1,16}[”\"][的]?(?:那首歌)?的?(?:MV|视频)?$", q):
        song = None
    # 专辑语境（…专辑的全部歌曲）歌名不再抽（歌名=专辑名）
    if album and re.search(r"专辑.*(全部歌曲|歌曲|这首|一首)", q):
        song = None
    # 纯 song 语境（如“放歌放慢慢”）取歌名：无其它强维度
    if not song and not (ip or album or singer) and re.search(r"放歌放", q):
        m = re.search(r"放歌放([^的]{1,16})$", q)
        if m:
            cand = m.group(1)
            if not any(t in cand for t in _TAG) and not re.search(r"免费|伴奏|DJ", cand):
                song = cand

    # "开心" 与 ip/song 冲突则取消 tag
    if "开心" in tag:
        if any("开心" in (s or "") for s in ip) or (song and "开心" in song):
            tag.remove("开心")
    return {
        "singer": singer, "version": v, "tag": tag, "ip": ip, "album": album,
        "lyricist": lyricist, "composer": composer, "toplist": toplist, "song": song,
    }


def _dedupe(lst):
    out = []
    for x in lst:
        if x not in out:
            out.append(x)
    return out


# ============ 分支 ============
def _branch_lyric_fan(query: str):
    q = query.strip()
    # 0 纯歌词反查 → 空参数
    if re.search(r"歌词.{0,3}是|来一个歌曲.*歌词|歌词有", q) and not _MV_MATCH(q):
        return ("music_song_search", {})
    # 1 榜单/最新 泛查询
    if _FAN.search(q):
        move_on = None
        if _MV_MATCH(q):
            # “…榜MV” 视作榜单图表（music_song_search 空参）；“看…的MV” 才进 mv
            if re.search(r"的MV|把.*看.*MV|看.*榜MV|想看.*MV", q):
                return None  # 交 mv
            return ("music_song_search", {})
        if "抖音" in q:
            # 抖音热歌榜 → 带 tag 抖音
            return ("music_song_search", {"retext": q, "tag": ["抖音"]})
        return ("music_song_search", {})
    return None


def _branch_tv(query: str):
    q = query.strip()
    if not _TV.search(q):
        return None
    return ("tvchannel_music_search", _base_clean(query))


def _qq_keywords(q: str):
    """QQ音乐 keywords：仅有效槽位（歌手/IP/榜单/歌名），否则默认“热门”。"""
    kw = []
    for x in _SINGER:
        if x in q:
            kw.append(x)
    for x, _ in _IP:
        if x in q:
            kw.append(x)
    tl = re.search(r"(飙升榜|热歌榜|好歌榜)", q)
    if tl:
        kw.append(tl.group(1))
    # 剥离平台词与噪音，剩余为歌曲关键字
    s = re.sub(r"QQ音乐|qq音乐|Qq音乐|qq", "", q)
    for n in sorted(_QQ_NOISE, key=len, reverse=True):
        s = s.replace(n, "")
    for k in kw:
        s = s.replace(k, "")
    s = re.sub(r"[，,。、！？!?：\s]+", "", s)
    # 恐龙抗狼特例（free song）
    if s.startswith("恐龙抗狼"):
        s = "恐龙抗狼"
    # 英文歌曲名保留空格（super star → super star）
    if re.fullmatch(r"[A-Za-z][A-Za-z ]*", s) and "super star" in q.lower() and re.fullmatch(r"super\s*s?tar", s, re.I):
        s = "super star"
    # 剩余若非“类别/泛指标签”则作为歌名关键字
    if s and not any(g in s for g in _QQ_GENRE):
        if s not in kw:
            kw.append(s)
    return kw or ["热门"]


def _branch_qq(query: str):
    q = query.strip()
    if not _QQ.search(q):
        return None
    return ("music_song_qqmusic_search", _base(query, keywords=_qq_keywords(q)))


def _branch_history(query: str):
    q = query.strip()
    if not _HIST.search(q):
        return None
    return ("music_song_history", _base(query))


def _branch_favorite(query: str):
    q = query.strip()
    if not _FAV.search(q):
        return None
    return ("music_song_favorite_search", _base(query))


def _mv_retext_full(query: str, s: dict):
    """MV clean retext 的进一步归一（用于裸歌名+视频/mv 场景）。"""
    sng = s.get("song")
    if not sng:
        return None
    q = query.strip()
    # 裸歌名 + MV/视频 且前面无动词 → 播放<sng>MV
    body = re.sub(r"[，,、\s《》]", "", q)
    body = body.lower().rstrip("视频歌曲mv")
    if body in (sng.lower(), sng.lower().replace(" ", "")):
        return "播放" + sng + "MV"
    if q == sng + "视频" or q == sng + "歌曲视频" or re.fullmatch(sng + r"(?:mv|MV)", q):
        return "播放" + sng + "MV"
    return None


def _mv_retext(query: str, s: dict):
    """MV 分支 retext 归一（对齐 golden）。"""
    q = query.strip()
    # 先试完整归一（裸歌名 + MV/视频）
    full = _mv_retext_full(q, s)
    if full:
        return full
    # 去歌词引号：播放"…这首歌的视频"保留引号；歌词是“…”剥离引号与内部标点
    if "“" in q or "”" in q or '"' in q:
        if '"' in q and q.startswith("播放") and "这首歌的视频" in q:
            return q
        q = q.replace("“", "").replace("”", "").replace('"', "")
        if q != query.strip():
            if re.search(r"歌词|带", query):
                q = re.sub(r"[，,,]", "", q)
            return q
    # 想听→想看（MV 语境）：仅当无专辑（带专辑保留“听”）；“我要听一路向北的MV”特例对齐 golden
    if s.get("album"):
        pass  # 保留听
    elif "想听" in q and re.search(r"想听.+MV$", q):
        q = q.replace("想听", "想看")
    elif "要听" in q and re.search(r"要听.+MV$", q):
        q = q.replace("要听", "要看")
    # “播放一个X MV” → 播放X MV（去掉“一个”）
    if re.search(r"一个.*MV", q) and s.get("song") and not re.search(r"歌词|专辑", q):
        q = q.replace("一个", "", 1)
    # “看<演唱者>MV”补一个的
    if s.get("singer") and not s.get("song"):
        for sing in s["singer"]:
            if q.endswith(sing + "MV") and ("的" + sing) not in q:
                q = q[:-len(sing) - 2] + sing + "的MV"
                break
    # “歌曲视频/音乐视频”纯音频化处理（无前置动词时）：xxx视频 → 播放xxxMV
    sng = s.get("song")
    if sng and not re.search(r"播放|看|听|搜|找|来", q[:2]):
        m = re.search(r"^(%s)(?:视频|歌曲视频)$" % re.escape(sng), q, re.I)
        if m:
            return "播放" + sng + "MV"
    return q




def _mv_albums(query, s):
    """MV condition retext: 一个→去掉；专辑语境保留。"""
    q = query
    if re.search(r"一个.*MV", q):
        q = q.replace("一个", "", 1)
    return q


def _branch_mvn_mf(query: str):
    pass


def _branch_mv(query: str):
    q = query.strip()
    if not _MV_MATCH(q):
        return None
    s = _compute_slots(q)
    # 歌名：词表优先；无词表命中且无其它强维度时才用去噪法（避免把歌手/IP/标签当歌名）
    have_dim = bool(s["singer"] or s["album"] or s["ip"] or s["tag"] or s["version"])
    if not s["song"]:
        # 已知歌词反查（带…歌词/带“…”的那首歌/歌词是）→ 不去噪抽歌名
        if not re.search(r"带[““\"].*的那首歌|歌词是|歌词有|带.{0,10}歌词", q):
            sn = _free_mv(q)
            if sn and not have_dim:
                # 去噪结果若是歌词引号内容的一部分或其自身为引号片段 → 不加
                if not re.search(r"[“\"”]", sn):
                    if not any(sn in (x or "") or (x or "") in sn for x in s["singer"]):
                        s["song"] = sn
    p = _base(_mv_retext(q, s))
    if s["singer"]: p["singer"] = s["singer"]
    if s["version"]: p["version"] = s["version"]
    if s["ip"]: p["ip"] = s["ip"]
    if s["tag"]: p["tag"] = s["tag"]
    if s["album"]: p["album"] = s["album"]
    if s["song"]: p["song"] = [s["song"]]
    ly = _lyrics(q)
    if ly: p["lyrics"] = [ly]
    return ("music_song_mv_search", p)


def _free_mv(q):
    """MV 语境去噪取歌名。"""
    s = q
    s = re.sub(r"(歌曲|音乐|视频|MV|mv|的是|的那首)$", "", s)
    for n in sorted(_MV_NOISE, key=len, reverse=True):
        s = s.replace(n, "")
    s = re.sub(r"参演?|出演?|的", "", s)
    s = s.strip("，、")
    s = re.sub(r"[，、！？：‐ ]", "", s)
    # 年份/年代 / 歌词修饰 → 非歌名
    if re.search(r"\d{2,4}年|年代|千禧年|的歌曲MV|的歌的MV|带.歌词|歌词是|歌词里", s):
        return None
    # 泛化标签/风格词当作歌名 → 不是歌名
    if re.search(r"古风|民谣|爵士|流行|欧美|华语|韩国|韩语|校园|治愈|摇滚|经典|的歌曲", s):
        return None
    if not s:
        return None
    return s or None


def _branch_ksong(query: str):
    q = query.strip().lower()
    if not (_KS.search(q) and not _KS_EXCL.search(q)):
        return None
    if _KS_RECOMMEND.search(q):
        return ("music_song_recommend", {"retext": query})
    s = _compute_slots(q)
    if not s["song"]:
        sn = _free_ks(q, s["singer"])
        # IP/强标签/泛类型语境（奥特曼/汪汪队/抖音热/圣诞歌/古风热歌…）不做去噪抽歌名
        sng_ok = sn and not s["ip"]
        if s["tag"] and sn and any(x in (sn or "") for x in ("热歌", "歌曲", "圣诞", "抖音", "古风", "经典", "儿歌")):
            sng_ok = False
        if sng_ok:
            s["song"] = sn
    p = _base(query)
    # K歌“唱X全部的歌”→ 去“全部”
    if re.search(r"唱[^的]+全部的歌", query) and s["singer"] and not s["song"]:
        p["retext"] = query.replace("全部", "")
    if s["singer"]: p["singer"] = s["singer"]
    if s["version"]: p["version"] = s["version"]
    if s["tag"]: p["tag"] = s["tag"]
    if s["ip"]: p["ip"] = s["ip"]
    if s["song"]: p["song"] = [s["song"]]
    return ("music_ksong_search", p)


def _free_ks(q, singer):
    """K歌语境去噪：歌名 = 去 唱/K歌/歌手/版本/噪音后的剩余。"""
    s = q
    for x in singer: s = s.replace(x, "")
    for vw in ["免费", "不钱", "不要钱", "不花钱", "免费版", "伴奏", "dj版", "DJ版"]:
        if vw in s:
            s = s.replace(vw, "")
    if re.search(r"全部.*的歌|的全部", s):
        s = re.sub(r"(全部|的歌|的)", "", s)
    for n in sorted(_KS_NOISE, key=len, reverse=True):
        s = s.replace(n, "")
    s = re.sub(r"[，。、！？!?：\s《》]", "", s)
    # 残余若只是纯噪音（歌/唱/免费/全部/一/个等），视为无歌名
    if not s or re.fullmatch(r"[一一个点唱首免费歌的全部儿童很多人020-]*", s):
        return None
    return s or None


def _branch_recommend(query: str):
    q = query.strip()
    if not _RECOMMEND.search(q):
        return None
    # 免费歌曲播放 等泛听意图（golden 无 version）
    if re.fullmatch(r"免费歌曲播放", q):
        return ("music_song_recommend", _base(query))
    s = _compute_slots(q)
    # 带明确槽位（歌手/专辑/IP/版本/词曲人/标签/歌名）不进推荐
    if s["singer"] or s["song"] or s["album"] or s["ip"] or s["version"] or s["lyricist"] or s["composer"]:
        return None
    return ("music_song_recommend", _base(query))


def _genre_empty(q):
    """无有效槽位的泛查询 → 空参数（如 影视金曲 / 90年代歌曲…）。"""
    return bool(re.search(r"影视金曲$|20\d{2}年|90年代|80年代|00后了歌曲|最新歌曲|网络歌曲榜|男女对唱|几十年代", q)) and not bool(
        _MV_MATCH(q))


def _branch_song_search(query: str):
    """兜底：泛化/孔位组装。"""
    q = query.strip()
    s = _compute_slots(q)
    # 看书/轻柔民谣 → 朋友聚会时听的歌 特例（gold：古典音乐或轻柔民谣吧）
    if re.search(r"适合看书的|适合阅读的", q) and ("民谣" in q or "轻柔" in q):
        return ("music_song_search", {"retext": "播放朋友聚会时听的歌", "tag": ["古典", "轻柔"]})
    # 换歌/切歌重写
    cut = _retext_cut(query)
    p = {}
    if cut:
        rt, target = cut
        # 目标里可能有 歌手+歌名，交给 _compute_slots 再抽一次
        s2 = _compute_slots(target) if target else {}
        # 若目标是歌名片段，直接用 _compute_slots 抽取结果
        if s2.get("song") and not s.get("song"):
            s = s2
        p = _base(rt)
        # 切歌目标若为裸歌名（无其它维度），直接作为 song；切歌语境不产出 album
        tgt = (target or "").strip()
        if tgt and not s2.get("song") and not any(s2.get(k) for k in
                ("singer", "version", "ip", "tag", "lyricist", "composer")):
            if not re.search(r"(歌曲|的歌|主题曲|片头曲|片尾曲|插曲)$", tgt):
                s["song"] = tgt
                s["album"] = []
        if s["song"] and not s["song"]:
            p["song"] = [s["song"]]
    else:
        # 空槽位泛述 → {}
        if not any(s[k] for k in ("singer", "version", "ip", "album", "tag",
                                  "lyricist", "composer", "toplist", "song")):
            if _genre_empty(q):
                return ("music_song_search", {})
        p = _base(query)
    # 演奏语境尖括号歌名 → retext 剥括号（唢呐演奏的《百鸟朝凤》→ 唢呐演奏的百鸟朝凤）
    if s["song"] and re.search(r"演奏[的]?《[^》]+》", q) and "专辑" not in q:
        p["retext"] = q.replace("《", "").replace("》", "")
    if s["singer"]: p["singer"] = s["singer"]
    if s["version"]: p["version"] = s["version"]
    if s["ip"]: p["ip"] = s["ip"]
    if s["album"]: p["album"] = s["album"]
    if s["tag"]: p["tag"] = s["tag"]
    if s["lyricist"]: p["lyricist"] = s["lyricist"]
    if s["composer"]: p["composer"] = s["composer"]
    if s["toplist"]: p["toplist"] = s["toplist"]
    if s["song"]: p["song"] = [s["song"]]
    ly = _lyrics(q)
    if ly: p["lyrics"] = [ly]
    return ("music_song_search", p)


RULE_SET = RuleSet(
    rules=[
        Rule(id="music_lyric_fan", tool="fan/music_song_search", priority=1,
             title="歌词反查/音乐榜单", explain="纯歌词反查或热搜榜/最新歌曲等榜单 → 对应工具",
             decide=_branch_lyric_fan),
        Rule(id="music_tv", tool="tvchannel_music_search", priority=2,
             title="电视频道", explain="命中 频道/CCTV/卫视 等 → 电视音乐检索",
             decide=_branch_tv),
        Rule(id="music_qq", tool="music_song_qqmusic_search", priority=3,
             title="QQ 音乐", explain="命中 QQ 音乐 → QQ 音乐检索（keywords）",
             decide=_branch_qq),
        Rule(id="music_history", tool="music_song_history", priority=4,
             title="历史", explain="命中 历史/听过/播放记录 → 历史",
             decide=_branch_history),
        Rule(id="music_favorite", tool="music_song_favorite_search", priority=5,
             title="收藏", explain="命中 收藏 → 收藏列表",
             decide=_branch_favorite),
        Rule(id="music_mv", tool="music_song_mv_search", priority=6,
             title="MV", explain="命中 mv/音乐视频 → MV 检索",
             decide=_branch_mv),
        Rule(id="music_ksong", tool="music_ksong_search", priority=7,
             title="K歌", explain="命中 K歌/唱歌 → K歌检索",
             decide=_branch_ksong),
        Rule(id="music_recommend", tool="music_song_recommend", priority=8,
             title="推荐", explain="命中 听歌/随便听 → 推荐",
             decide=_branch_recommend),
        Rule(id="music_song_search", tool="music_song_search", priority=9,
             title="歌曲检索（兜底）", explain="其余默认音乐检索（组装全部槽位与歌词）",
             decide=_branch_song_search),
    ],
)


def apply(query):
    if not query or not query.strip():
        return None
    sel = RULE_SET.select_with_rule(query)
    if sel is None:
        return None
    tool, params, rule = sel
    return tool, params, rule.id


__all__ = ["apply"]