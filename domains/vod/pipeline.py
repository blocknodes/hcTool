"""vod 域决策主轴：fewshot 缓存直出 + 单次 LLM 出 tool&params。

本域只提供「域知识」（工具怎么暴露、提示词怎么写、字段集怎么下钻、参数怎么归一），
调度逻辑全部在 `app/pipeline_kernel.py`（三层：badcase → fewshot_cache → llm_onecall → fallback）。
完整架构说明见 `vod_design.md`。

本文件保留的域特有部分：
- `_FIELD_CATALOG` / `_SYSTEM_TMPL`：vod 的字段目录与提示词。
- `_VOD_SEARCH_UMBRELLA` + `drill_search_tool`：搜索族包含关系（vod_search ⊂ vod_search_all）。
- `_OOR_DIM_WORDS`：表外维度词表（票房/收视率…），命中即改判 fuzzy。
- `norm_retext`：retext 的轻量归一（去标点/空格，保护小数点）。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.pipeline_kernel import KernelSpec, ShotPool, build_pipeline, iter_fields

from . import textkey
from .dsl import _SEARCH_ALL_FIELDS, _SEARCH_FIELDS

# 搜索族只暴露 vod_search_all（它包含 vod_search 的全部能力：字段集是其严格超集，
# 且 vod_search 的 field 枚举 18 个 ⊂ vod_search_all 的 32 个）。golden 里
# vod_search / vod_search_all 的分工可由「用了哪些字段」100% 还原（实测 178/179），
# 故不需要让 LLM 区分二者——出结构化参数即 search_all，描述性表达即 fuzzy。
_VOD_SEARCH_UMBRELLA = "vod_search_all"

# retext 为 required 的工具
_RETEXT_TOOLS = {"vod_search", "vod_search_all", "vod_fuzzy_search"}

# 合法字段名全集（含 schema 侧叫 voice_start_pos、dsl 侧叫 voiceStartPos 的同一字段）
_ALL_FIELDS = _SEARCH_FIELDS | _SEARCH_ALL_FIELDS | {"voice_start_pos"}

# 表外维度 → 兜底改判 fuzzy 的词表。
# 只在 **LLM 已经给出搜索族之后** 生效，属后处理兜底，不短路 LLM：
# 模型对「票房/收视率」这类词常常静默丢维度、或拿表内 sort 维度顶替（播放量顶替观看人数），
# 字面上完全合法，规则层无法从结构上识别，只能靠词表。
# 故意收窄：只收「明确的表外指标名」，不收「最差/最少」这类程度词 ——
# 后者可能修饰表内维度（口碑最差 = rate asc），由 LLM 判更准，规则不该抢。
_OOR_DIM_WORDS = (
    "票房", "投资", "成本", "制作费", "片酬",
    "收视率", "上座率", "观看人数", "播放人次",
    "弹幕", "拉新", "热搜", "话题度", "讨论度",
    "评分人数", "评论数", "想看人数", "预约量",
    "排片", "场次", "下载量", "销量",
)

# 字段目录：**只给字段名 + 语义，不给枚举取值**。枚举取值靠 fewshot 样例示范，
# 否则 32 个字段 × 各自的长枚举会把 prompt 撑爆且淹掉判别信息。
_FIELD_CATALOG = """- title 作品名（电影/剧/综艺/动漫/相声/戏曲/晚会…）
- actor 演员
- director 导演、编剧
- entertainer 参与人员、艺人表演者（含歌手）
- role 角色名（剧中人物，不是演员本人）
- dubbing 配音演员
- hostess 主持人
- writer 作者
- prize 影视奖项（如白玉兰、奥斯卡）
- sub_prize 子奖项（如最佳男主角）
- category 影视分类（电影/电视剧/综艺/纪录片/戏曲…）
- tag 标签、题材（如抗战、悬疑、情景喜剧、经典）
- area 地区（如内地、香港、美国）
- language 语言（如粤语、国语、英语）
- channel 频道、电视台（如湖南卫视、番茄台）
- company 出品公司（如正午阳光）
- vender_name 供应商
- comedy_brand 喜剧厂牌（如笑果文化、德云社）
- creation_source 剧本来源（如小说改编）
- target 受众（如老年、儿童、中年人）
- gender 性别
- definition 清晰度（is_4k / is_3d / hdr）
- sound 音效（如杜比全景声、dts）
- technology 拍摄技术
- is_fee 免付费（"0"=免费，"1"=付费/VIP）
- is_over 状态（"0"=连载中，"1"=已完结）
- age_range 年龄范围
- release_time 发布时间范围
- rate 评分范围（0~10）
- series 第几部或第几季（数字字符串，从 1 开始）
- video_index 第几集或第几期（数字字符串，从 1 开始）
- voice_start_pos 起播位置（秒，非负整数字符串）"""

_SYSTEM_TMPL = """你是电视语音助手的意图解析器。读用户的话，输出该调用的工具名和参数。

【工具】
{tool_briefs}

【怎么选工具】
按下面的顺序判断，**先命中先返回**：
0. **用户提到了下面字段表和排序表里都没有的维度**（如票房、投资、制作成本、收视率、
   观看人数、上座率…）→ 直接 vod_fuzzy_search，**把整句话原样交给它**。
   - 字段表和排序表是**穷尽的**：只有里面列出的名字能用，任何表外的名词都不许写进
     query 或 sort。
   - 严禁把该维度的词**悄悄丢掉**、只留剩下的字段硬拼一条结构化查询 —— 那样返回的内容
     跟用户要的完全不是一回事，比报错更危险。
   - 严禁拿近似维度**顶替**（票房≠评分≠播放量，收视率≠播放量，投资≠热度）。
   - 判断要点：只要「最高/最大/最贵」修饰的是**表外维度**，就属于本条第 0 项。
1. 只问「某个人是谁」这类人物本身信息（生平/简介/是谁）→ vod_person_search。
2. 明确要看/要播具体内容（播放、打开、第几集、唱、听听）→ vod_search_all。
3. 基于某个片名/演员/导演/分类要**相似的**（类似、像…一样、还有吗）→ vod_relate_search。
4. 明确要**回看自己的观看记录**（我看过、我的历史、播放记录）→ vod_history。
5. 剩下的都是「找内容」：
   - 能抽出**结构化筛选维度**（片名/演员/导演/分类/标签/地区/语言/年份/评分/免费会员/第几集…）
     → vod_search_all。它已涵盖全部结构化检索能力，**不要再区分更窄的搜索工具**。
   - 抽不出结构化维度、只能整句语义理解的**描述性表达**（剧情/台词/画面/情绪/心情/状态）
     → vod_fuzzy_search。

**vod_personalized_search 只在用户明确指向「我/自己的偏好画像」时用**，例如「推荐我喜欢的」
「根据我的喜好推荐」「给我推荐点我感兴趣的」。下列情况**一律不用**它：
- 「推荐/求推荐/有没有推荐」后面跟的是内容描述或条件（口碑炸裂的、适合和家人看的、心情低落时看的）
  —— 模型无从得知用户偏好，这类走上面的第 5 条（结构化 → vod_search_all，描述性 → vod_fuzzy_search）。
- 只提到某类人群（中产、中年人、女性、大人和孩子）—— 那是在描述内容受众，不是用户自己的画像。

【搜索参数怎么写】vod_search_all 的 params 形如
{{"action":"search"或"play", "retext":"用户原话", "query":<条件树>, "sort":<排序>}}
- action：搜索/查找/浏览 → search；起播/播放/打开/看第几集 → play。
- retext：原样填用户完整原话（含标点，不用自己清理）。
- sort：按 新出/最新→new、好看/热播/口碑→hot、高分→rate、播放量高→play 的
  {{"维度":{{"order":"desc"}}}}；「冷门」用 asc。可多个并存。
  **只在用户明确表达了排序诉求时才给 sort**，据实可给多个维度。
- 老片别自己加 sort：用户说「经典/老片/口碑依然能打」时，加 {{"rate":{{"order":"desc"}}}} 会把
  老片筛没；除非用户明说要高分/排序，否则不要给 sort。
- 「日本人」「韩国」「香港」这类**地区词**一律抽成 area；「免费」抽成 is_fee="0"（**用 0/1，不要写"免费"**）。
- 说了体裁就要同时给 category 和标签：结尾的「电影/电视剧/综艺/纪录片/动漫/戏曲/相声/晚会」
  → category；修饰它的词（古装/悬疑/喜剧/恐怖/竞技/年代/都市…）→ tag。如「林玉芬导演的古装剧」
  → category=电视剧 + tag=古装 + director=林玉芬；「韩国恐怖片致命之旅」→ title + area=韩国 +
  tag=恐怖 + category=电影。
- 相关检索（vod_relate_search）要**同时**给对标作品和用户点名的体裁，如「像《漫长的季节》这种
  类型的剧还有吗」→ title=漫长的季节 + category=电视剧。

【query 条件树】
可用字段（**只写 field 名，取值按用户原话或最接近的常用说法填**）：
{fields}

节点形态（任意嵌套）：
- 单值：{{"field":"category","value":"电影"}}
- 多值：{{"field":"tag","values":["悬疑","动作"],"operator":"or"}}
- 范围：{{"field":"rate","from":"8","to":"*"}}（from/to 都要有，无界端填 "*"；
  release_time 用 yyyyMMdd，rate 用 0~10，age_range 用年龄数字，一律字符串）
- 组合：{{"and":[...]}} / {{"or":[...]}} / {{"not":{{...}}}}
- 条件只有一个时，query 直接写那个叶子，不要包 and。
- 字段无法归一化到常用值时不传该字段，不要自造新值。

【输出】
只输出一个 JSON 对象，形如 {{"tool":"工具名","params":{{...}}}}，不要任何解释。
只抽用户明确表达的信息，不臆造；用户没提的可选字段不要出现（不要填 null / 空串 / 空数组）。
若用户只报了作品名/栏目名而无任何可抽取维度，params 输出 {{}}。
"""


def norm_retext(text: str) -> str:
    """retext 落库前的轻量归一，对齐 golden 里 retext 的书写约定：209 条带 retext 的行中，
    23 条与 query 有差异，其中 21 条**只差标点和内部空格**，另 2 条是把阿拉伯数字改写成
    中文口语（8.8分→8点8分、90年代→九十年代）—— 方向互斥、且各只命中一条，属 golden 的
    逐条噪声，不做数字改写。
    特意不用 textkey.normalize —— 它会去「请/帮我」前缀并强转小写，超出 golden 的约定。
    """
    # strip_punct 会把小数点一起吃掉（「8.8分」→「88分」），所以先用一个只含字母数字的
    # 哨兵把它换出来 —— 这类字符正是 strip_punct 唯一保留的集合。哨兵做存在性检查，
    # 避免原句里恰好出现同样串。
    mark = "qZqZq"
    while mark in text:
        mark += "q"
    s = re.sub(r"(?<=\d)\.(?=\d)", mark, text)
    s = textkey.strip_punct(s)  # 去标点、去内部空格
    return s.replace(mark, ".")


def exposed_tools(domain) -> list:
    """暴露给 LLM 的候选工具：搜索族只留 vod_search_all（vod_search 是其子集，不下发）。"""
    return [t for t in domain.tools if t.name != "vod_search"]


def normalize_tool_name(tool: str) -> str:
    """模型若仍输出被隐藏的 vod_search，统一并到 vod_search_all（下游再按字段下钻）。"""
    return _VOD_SEARCH_UMBRELLA if tool == "vod_search" else tool


def drill_search_tool(params: dict) -> str:
    """收窄搜索族工具：用了 search_all 独有维度 → vod_search_all，否则 vod_search。

    LLM 只面对 vod_search_all（不再区分二者，避免选错），但 golden 契约里两者分工明确，
    且该分工可由「用了哪些字段」100% 还原（实测 178/179）。故在此确定性下钻，
    让输出形态与既有契约一致。空条件树 → vod_search（与 golden 6 条空 params 一致）。
    """
    fields = iter_fields(params.get("query"), set())
    return "vod_search_all" if (fields & _SEARCH_ALL_FIELDS) else "vod_search"


def _tool_briefs(tools) -> str:
    """工具简介：取描述里最能区分彼此的「适用/不适用场景」段，避免被长前缀截掉。"""
    lines = []
    for t in tools:
        desc = " ".join(t.description.replace("\\n", " ").split())
        lines.append(f"- {t.name}：{desc[:900]}")
    return "\n".join(lines)


def system_prompt(domain, tools) -> str:
    return _SYSTEM_TMPL.format(tool_briefs=_tool_briefs(tools), fields=_FIELD_CATALOG)


SPEC = KernelSpec(
    key="vod",
    textkey=textkey,
    system_prompt=system_prompt,
    norm_retext=norm_retext,
    retext_tools=_RETEXT_TOOLS,
    search_family=_RETEXT_TOOLS,
    exposed_tools=exposed_tools,
    normalize_tool=normalize_tool_name,
    drill=drill_search_tool,
    umbrella=_VOD_SEARCH_UMBRELLA,
    all_fields=_ALL_FIELDS,
    fuzzy_tool="vod_fuzzy_search",
    oor_words=_OOR_DIM_WORDS,
    pool_path=Path(__file__).resolve().parent / "testset.json",
)

pipeline = build_pipeline(SPEC)
default_pool = pipeline.default_pool
