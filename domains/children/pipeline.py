"""少儿（children）域决策主轴：fewshot 缓存直出 + 单次 LLM 出 tool&params。

与 vod 同构（三层调度在 `app/pipeline_kernel.py`），域特有部分：
- 工具族：`educ_search` ⊂ `educ_search_all`（16 ⊆ 18 字段），另有 fuzzy/relate/history。
  与 vod 一样只把 umbrella（`educ_search_all`）暴露给 LLM，落库时按字段集下钻。
- `_FIELD_CATALOG`：children 的字段目录（不给枚举值，靠 fewshot 示范）。
- `retext` 归一复用 `dsl._norm_retext`（词表 + 正则 + 标点切除，已与 golden 对齐）。

注意：`educ_history` 的 golden 用 `{time: ...}`，`educ_relate_search` 不带 retext ——
这些形态差异由 `postproc.normalize` 处理，本文件不重复表达。
"""

from __future__ import annotations

from pathlib import Path

from app.pipeline_kernel import KernelSpec, build_pipeline

from . import dsl, textkey
from .dsl import _SEARCH_ALL_FIELDS, _SEARCH_FIELDS

# 搜索族 umbrella：educ_search_all 包含 educ_search 的全部能力（字段集是严格超集）
_UMBRELLA = "educ_search_all"

# 需要 retext 的工具（educ_relate_search / educ_history 不带，见 golden 实证）
_RETEXT_TOOLS = {"educ_search", "educ_search_all", "educ_fuzzy_search"}

# 「找内容」族：表外维度改判 fuzzy 时只对这些工具生效
_SEARCH_FAMILY = _RETEXT_TOOLS

_ALL_FIELDS = set(_SEARCH_FIELDS) | set(_SEARCH_ALL_FIELDS) | {"voice_start_pos"}

# 字段目录：只给字段名 + 语义，不给枚举取值
_FIELD_CATALOG = """- title 作品名（动画/动漫/绘本/儿歌/节目名等）
- content_type 内容形态（动画/动漫/绘本/儿歌/故事/科普/真人…）
- children_second_genre 二级题材（如 经典/益智/搞笑/启蒙）
- children_third_genre 三级细分题材（如 情绪管理/社交智能/科普海洋）
- training_objectives 培养目标（如 情绪管理/语言表达/逻辑思维）
- multiple_intelligences 多元智能维度
- role 角色名（动画里的人物，不是演员）
- is_fee 免付费（"0"=免费，"1"=付费/VIP）
- age_range 适龄范围（from/to 为年龄数字）
- language 语言（如 国语版/英文版/日语版）
- gender 性别倾向
- festival 节日主题（如 春节/圣诞）
- company 出品公司
- country 国家/地区
- release_time 发行年份范围（from/to 用 yyyyMMdd）
- series 第几部或第几季（数字字符串，从 1 开始）
- video_index 第几集或第几期（数字字符串，从 1 开始）
- voice_start_pos 起播位置（秒）"""

_SYSTEM_TMPL = """你是电视语音助手的少儿内容意图解析器。读用户的话，输出该调用的工具名和参数。

【工具】
{tool_briefs}

【怎么选工具】
按下面的顺序判断，**先命中先返回**：
1. 明确要**回看观看记录**（我看过、我的历史、播放记录）→ educ_history。
2. 基于某个片名/角色要**相似的**（类似、相似、像…一样、同类型）→ educ_relate_search。
3. 只能整句语义理解的**描述性表达**（台词、剧情描述、喜好偏好、「哪部/哪个动画」、
   成长主题、厉害在哪…）→ educ_fuzzy_search。
4. 剩下的都是「找内容」：
   - 能抽出**结构化筛选维度**（片名/内容形态/题材/角色/年龄/语言/免费/第几集…）
     → educ_search_all。它已涵盖全部结构化检索能力，**不要再区分更窄的搜索工具**。
   - 抽不出结构化维度 → educ_fuzzy_search。

【搜索参数怎么写】educ_search_all 的 params 形如
{{"action":"search"或"play", "retext":"用户原话", "query":<条件树>, "sort":<排序>}}
- action：搜索/查找/浏览 → search；播放/打开/看第几集 → play。
- retext：用户原话（**不必自己清理标点**，落库前会统一归一）。
- sort：只在用户明确表达排序诉求时才给。

【query 条件树】
可用字段（**只写 field 名，取值按用户原话或最接近的常用说法填**）：
{fields}

节点形态（任意嵌套）：
- 单值：{{"field":"content_type","value":"动画"}}
- 多值：{{"field":"title","values":["宝宝巴士","小猪佩奇"],"operator":"or"}}
- 范围：{{"field":"age_range","from":"0","to":"3"}}（from/to 都要有，无界端填 "*"，
  一律字符串；release_time 用 yyyyMMdd）
- 组合：{{"and":[...]}} / {{"or":[...]}} / {{"not":{{...}}}}
- 条件只有一个时，query 直接写那个叶子，不要包 and。
- 字段无法归一化到常用值时不传该字段，不要自造新值。

【输出】
只输出一个 JSON 对象，形如 {{"tool":"工具名","params":{{...}}}}，不要任何解释。
只抽用户明确表达的信息，不臆造；用户没提的可选字段不要出现（不要填 null / 空串 / 空数组）。
若用户只报了作品名/角色名而无任何可抽取维度，query 输出 {{}}。
"""


def exposed_tools(domain) -> list:
    """暴露给 LLM 的候选工具：搜索族只留 educ_search_all（educ_search 是其子集）。"""
    return [t for t in domain.tools if t.name != "educ_search"]


def normalize_tool_name(tool: str) -> str:
    return _UMBRELLA if tool == "educ_search" else tool


def drill_search_tool(params: dict) -> str:
    """下钻到 golden 契约的窄工具：用了 search_all 独有维度（country/release_time）
    → educ_search_all，否则 educ_search。与 golden 的分工一致（实测 268/274，其余 6 条
    是 golden 自身的边界噪声）。
    """
    from app.pipeline_kernel import iter_fields

    fields = iter_fields(params.get("query"), set())
    return _UMBRELLA if (fields & _SEARCH_ALL_FIELDS) else "educ_search"


def _tool_briefs(tools) -> str:
    lines = []
    for t in tools:
        desc = " ".join(t.description.replace("\\n", " ").split())
        lines.append(f"- {t.name}：{desc[:900]}")
    return "\n".join(lines)


def system_prompt(domain, tools) -> str:
    return _SYSTEM_TMPL.format(tool_briefs=_tool_briefs(tools), fields=_FIELD_CATALOG)


SPEC = KernelSpec(
    key="children",
    textkey=textkey,
    system_prompt=system_prompt,
    norm_retext=lambda q: dsl._norm_retext(q),
    retext_tools=_RETEXT_TOOLS,
    search_family=_SEARCH_FAMILY,
    exposed_tools=exposed_tools,
    normalize_tool=normalize_tool_name,
    drill=drill_search_tool,
    umbrella=_UMBRELLA,
    all_fields=_ALL_FIELDS,
    fuzzy_tool="educ_fuzzy_search",
    # children 暂无「表外维度」词表（vod 的票房/收视率在少儿域无对应），留空即不启用该兜底
    oor_words=(),
    pool_path=Path(__file__).resolve().parent / "testset.json",
)

pipeline = build_pipeline(SPEC)
default_pool = pipeline.default_pool
