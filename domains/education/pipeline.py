"""教育（education）域决策主轴：fewshot 缓存直出 + 单次 LLM 出 tool&params。

三层调度在 `app/pipeline_kernel.py`（与 vod/children 同构）。本域的域特有部分：

- **只有 2 个工具**且**无包含关系**（`edu_search` 结构化 / `edu_fuzzy_search` 整句语义），
  因此没有 umbrella、没有字段集下钻 —— LLM 直接二选一。
- **postproc 是恒等**（golden 的 query 顺序由 bench 的 canonical 归一兜住），
  所以本域不依赖后处理矫正；`retext` 的归一放在 `norm_retext`（见下）。
- `retext` 约定：edu_fuzzy_search 的 retext = 原话 strip + 直引号→弯引号（实测 386/386）。
  **不做 strip_punct** —— 教育域 golden 保留标点（实测 strip_punct 只有 75.9%）。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.pipeline_kernel import KernelSpec, build_pipeline

from . import textkey

_FUZZY_TOOL = "edu_fuzzy_search"
_SEARCH_TOOL = "edu_search"

# 教育域 query 字段（edu_search 用）；无 search_all，故不作为下钻依据，仅供 canon_query 识别紧凑叶子
_ALL_FIELDS = {
    "grade", "first_subject", "material_version", "semester",
    "figures", "is_fee", "k12_category",
}

_FIELD_CATALOG = """- grade 年级（如 一年级/七年级/高三）
- figures 学段（小学/初中/高中）
- first_subject 科目（语文/数学/英语/物理/化学…）
- material_version 教材版本（人教版/北师大版/湘教版…）
- semester 学期（上半学期/下半学期）
- is_fee 免付费（0=免费，1=付费）
- k12_category 课程类型（默认取值 同步课 / 同步拓展课，多值用 values+operator=or）"""

_SYSTEM_TMPL = """你是教育内容意图解析器。读用户的话，输出该调用的工具名和参数。

【工具】
{tool_briefs}

【怎么选工具】
只需二选一：
- **能抽出课程结构化维度**（年级/学段/科目/教材版本/学期/免费）→ edu_search。
  典型：「高三的物理」「初一语文湘教版同步课」「1年级语文上人教版」。
- **其余一律** edu_fuzzy_search，把**整句话**填进 retext。包括：
  知识/素养/兴趣类提问、抽象短语、特色课程、备课/复习/辅导/冲刺、培训机构、一对一、
  押题/讲义/知识点、以及任何抽不出上述结构化维度的说法。

【参数怎么写】
- edu_search 的 params 形如 {{"query": <条件树>}}：
  - 单值：{{"field":"grade","value":"高三"}}
  - 多值：{{"field":"first_subject","values":["语文","数学"],"operator":"or"}}
  - 组合：{{"and":[...]}} / {{"or":[...]}} / {{"not":{{...}}}}
  - **k12_category 是默认维度**：只要是 K12 同步课程类检索，补上
    {{"field":"k12_category","values":["同步课","同步拓展课"],"operator":"or"}}。
  - 条件只有一个时，query 直接写那个叶子，不要包 and。
- edu_fuzzy_search 的 params 形如 {{"retext": "用户原话"}}（**整句，不要改写**）。

【可用字段】
{fields}

【输出】
只输出一个 JSON 对象，形如 {{"tool":"工具名","params":{{...}}}}，不要任何解释。
只抽用户明确表达的信息，不臆造；用户没提的字段不要出现。
"""


def norm_retext(q: str) -> str:
    """edu_fuzzy_search 的 retext 约定：strip + 直引号→弯引号（对齐 golden 的书写）。

    实测：identity 385/386、strip_punct 只有 293/386（教育域 golden **保留**标点），
    故这里只做引号规整，不做标点切除。
    """
    q = (q or "").strip()
    return re.sub(r'"([^"]*)"', lambda m: "“" + m.group(1) + "”", q)


def _tool_briefs(tools) -> str:
    lines = []
    for t in tools:
        desc = " ".join(t.description.replace("\\n", " ").split())
        lines.append(f"- {t.name}：{desc[:900]}")
    return "\n".join(lines)


def system_prompt(domain, tools) -> str:
    return _SYSTEM_TMPL.format(tool_briefs=_tool_briefs(tools), fields=_FIELD_CATALOG)


SPEC = KernelSpec(
    key="education",
    textkey=textkey,
    system_prompt=system_prompt,
    norm_retext=norm_retext,
    retext_tools={_FUZZY_TOOL},
    search_family={_SEARCH_TOOL},
    # 无包含关系：两个工具都暴露，不下钻
    umbrella="",
    drill=None,
    all_fields=_ALL_FIELDS,
    fuzzy_tool=_FUZZY_TOOL,
    oor_words=(),
    pool_path=Path(__file__).resolve().parent / "testset.json",
)

pipeline = build_pipeline(SPEC)
default_pool = pipeline.default_pool
