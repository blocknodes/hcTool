"""教育（education）域确定性规则层（L1）。

工具 3 个：
- edu_search：K12 课程结构化检索（嵌套 DSL：grade/first_subject/semester/
  material_version/figures/is_fee/k12_category）。
- edu_fuzzy_search：非 K12/特色课程/抽象短语 → query=原话整句检索。
- edu_slow_search_data_search：极少数"备课视频"类（1 条 golden）。

判定顺序：
1. 可拆出年级/科目等的 K12 结构化 → edu_search（DSL 组装）。
2. 无法结构化（吉他课/围棋/书法/期末复习范围/知识点/押题…抽象或非 K12）→ edu_fuzzy_search。

对 216 条 golden 实证收敛。
"""
from __future__ import annotations

import re

from app.rulebase import Rule, RuleSet

# ---------- 字段归一 ----------
# 年级：口语 → 标准文（初一→七年级，初二→八年级，初三→九年级，一~九年级，高一/高二/高三）
_GRADE_MAP = [
    # 初中口语
    (r"初一", "七年级"), (r"初二", "八年级"), (r"初三", "九年级"),
    (r"初中七", "七年级"), (r"初中八", "八年级"), (r"初中九", "九年级"),
    # 高中
    (r"高一", "高一"), (r"高二", "高二"), (r"高三", "高三"),
    # 高中三年级/二年级/一年级
    (r"高中三年级", "高三"), (r"高中二年级", "高二"), (r"高中一年级", "高一"),
    # 九年义务教育数字/中文年级
    ]
# 数字年级：1年级→一年级 … 9年级→九年级；7年级→七年级
_GRADE_NUM = {
    "1": "一年级", "2": "二年级", "3": "三年级", "4": "四年级",
    "5": "五年级", "6": "六年级", "7": "七年级", "8": "八年级", "9": "九年级",
}

# 科目
_SUBJECT_MAP = [
    (r"语文", "语文"), (r"数学", "数学"), (r"英语", "英语"),
    (r"物理", "物理"), (r"化学", "化学"), (r"生物", "生物"),
    (r"历史", "历史"), (r"地理", "地理"), (r"政治|道法", "政治"),
    (r"科学|理化", "科学"),
]

# 教育阶段（figures）：小学/初中/高中（与 grade 独立）
_FIGURE_MAP = [(r"小学", "小学"), (r"初中", "初中"), (r"高中", "高中")]

# 教材版本
_VERSION_MAP = {
    "人教版": "人教版", "湘教版": "湘教版", "北师大版": "北师大版", "沪科版": "沪科版",
    "苏教版": "苏教版", "青岛版": "青岛版", "外研版": "外研版", "鄂教版": "鄂教版",
    "人民教育出版社": "人教版",
}

# 学期
_SEMESTER_MAP = [(r"上册|上半学期|上?学?期", "上半学期"),
                 (r"下册|下半学期|下?学?期", "下半学期")]


def _first_in(q: str, pats: list[tuple]) -> str | None:
    """按出现位置取最靠前命中的口语→标准。"""
    best, bestpos = None, len(q) + 1
    for pat, norm in pats:
        m = re.search(pat, q)
        if m and m.start() < bestpos:
            best, bestpos = norm, m.start()
    return best


def _grade(q: str) -> str | None:
    """提取年级。返回标准值（七年级/八年级/…/高三）。多值用 _grades。"""
    # 高中三年级/初中三年级 等长词优先
    for pat, norm in (
            (r"初中三年级", "九年级"), (r"高中三年级", "高三"), (r"高中二年级", "高二"),
            (r"高中一年级", "高一"), (r"初三年级", "九年级"), (r"初二年级", "八年级"),
            (r"初一年级", "七年级"), (r"初中三年级", "九年级"), (r"初中二年级", "八年级"), (r"初中一年级", "七年级"),
            (r"初一", "七年级"), (r"初二", "八年级"), (r"初三", "九年级"),
    ):
        if re.search(pat, q):
            return norm
    # 高N年级 → 高二 等
    m = re.search(r"高([一二三])年级", q)
    if m:
        return {"一": "高一", "二": "高二", "三": "高三"}[m.group(1)]
    # 高2年级 → 高二
    m = re.search(r"高([123])年级", q)
    if m:
        return {"1": "高一", "2": "高二", "3": "高三"}[m.group(1)]
    # 数字师范：1年级 等
    m = re.search(r"([1-9])年级", q)
    if m:
        return _GRADE_NUM[m.group(1)]
    # 汉字年级
    for ch, s in (("一", "一年级"), ("二", "二年级"), ("三", "三年级"), ("四", "四年级"),
                  ("五", "五年级"), ("六", "六年级"), ("七", "七年级"), ("八", "八年级"), ("九", "九年级")):
        if re.search(f"{ch}年级", q):
            return s
    # 高中骨
    for pat in ("高一", "高二", "高三"):
        if re.search(pat, q):
            return pat
    return None


def _grades_multi(q: str) -> list[str] | None:
    """连列多个年级（高一高二高三、小学三年级和四年级）→ list；否则 None。"""
    # 仅当连列≥2 才触发 multi（避免误把单个证据当多值）
    if not re.search(r"一二年级|(三年|二年|一年)[一二三四五六七八九十]年级|(高一|高二|高三).{0,2}(高一|高二|高三)|"
                     r"[一二三四五六七八九]年级(?:[上中下]册|.{0,4}学期)?.{0,3}(和|与|、).{0,3}[一二三四五六七八九]年级(?:[上中下]册)?|"
                     r"(初一|初二|初三)(和|与|、)(初一|初二|初三)|"
                     r"小学[一二三四五六]年级和小学[一二三四五六]年级", q):
        return None
    found = []
    # 相邻短式：小学一二年级 / 一二年级 → [一年级,二年级]
    m = re.search(r"([一二三四五六])([一二三四五六])年级", q)
    if m:
        d = {"一": "一年级", "二": "二年级", "三": "三年级", "四": "四年级", "五": "五年级", "六": "六年级"}
        found = [d[m.group(1)], d[m.group(2)]]
        return found
    for pat, norm in (("高一", "高一"), ("高二", "高二"), ("高三", "高三"),
                      ("初一", "七年级"), ("初二", "八年级"), ("初三", "九年级"),
                      ("一年级", "一年级"), ("二年级", "二年级"), ("三年级", "三年级"),
                      ("四年级", "四年级"), ("五年级", "五年级"), ("六年级", "六年级"),
                      ("七年级", "七年级"), ("八年级", "八年级"), ("九年级", "九年级"),
                      ("小学一年级", "一年级"), ("小学二年级", "二年级"),
                      ("小学三年级", "三年级"), ("小学四年级", "四年级"),
                      ("小学五年级", "五年级"), ("小学六年级", "六年级")):
        if re.search(pat, q) and norm not in found:
            found.append(norm)
    return found or None


def _subjects(q: str) -> list[str]:
    subj = []
    seen = set()
    # 完整科目词（"数学或物理" 连词拆分）
    for pat, norm in _SUBJECT_MAP:
        for m in re.finditer(pat, q):
            if norm not in seen:
                seen.add(norm)
                subj.append(norm)
    return subj


def _semester(q: str) -> str | None:
    # 上册/下册；上半学期/下半学期
    if re.search(r"下册|下半学期|下学期|下册|下册", q):
        return "下半学期"
    if re.search(r"上册|上半学期|上学期", q):
        return "上半学期"
    m = re.search(r"(?:上|下)册", q)
    if m:
        return "下半学期" if "下" in m.group(0) else "上半学期"
    # 独立 "上/下" 紧跟在科目词后（如 语文上人教版 → 上册）
    if re.search(r"(语文|数学|英语|物理|化学|生物|历史|地理|政治|科学)上", q):
        return "上半学期"
    if re.search(r"(语文|数学|英语|物理|化学|生物|历史|地理|政治|科学)下", q):
        return "下半学期"
    return None


def _version(q: str) -> list[str] | None:
    found = []
    seen = set()
    for k, v in _VERSION_MAP.items():
        if k in q and v not in seen:
            seen.add(v)
            found.append(v)
    return found or None


def _figures_multi(q: str) -> list[str]:
    """提取阶段（小学/初中/高中），支持多阶段（初中和高中）。"""
    found = []
    for pat, norm in _FIGURE_MAP:
        if re.search(pat, q) and norm not in found:
            found.append(norm)
    return found


def _is_free(q: str) -> int | None:
    # "不要免费的" 特殊 → 收费(1)
    if "不要免费" in q or "不要免费" in q or "不免费" in q or "但不要免费的" in q:
        return 1
    if re.search(r"免费|不花钱|不要钱|免费看", q):
        return 0
    if re.search(r"收费|付费|要收费|要付费|要钱|会员|VIP|不是免费", q) or ("不要免费" in q):
        return 1
    return None


def _is_structured(q: str) -> bool:
    """是否是 K12 结构化（有年级/科目/版本/阶段/学期之一）。"""
    return bool(_grade(q) or _subjects(q) or _version(q) or _figures_multi(q) or _semester(q))


def build_dsl(q: str) -> dict | None:
    """组装 edu_structure DSL。返回 {query: {and:[...]}} 或 None。"""
    conds = []
    def add(field, value):
        if value is None:
            return
        if isinstance(value, list) and not value:
            return
        conds.append({"field": field, "value": value})
    def add_multi(field, values):
        if values:
            conds.append({"field": field, "values": values, "operator": "or"})

    gs = _grades_multi(q)
    g = _grade(q) if not gs else None
    if gs:
        add_multi("grade", gs)
    elif g:
        add("grade", g)

    # figures：仅当无显式年级时才带（小学/初中/高中），支持多阶段（初中和高中）
    figs = _figures_multi(q)
    if not (g or gs) and figs:
        if len(figs) == 1:
            add("figures", figs[0])
        else:
            add_multi("figures", figs)

    sem = _semester(q)
    if sem:
        if re.search(r"上册.{0,3}下册|下册.{0,3}上册|上册和|下册和|上下册", q):
            add_multi("semester", ["上半学期", "下半学期"])
        else:
            add("semester", sem)
    vers = _version(q)
    if vers:
        if len(vers) == 1:
            add("material_version", vers[0])
        else:
            add_multi("material_version", vers)
    fee = _is_free(q)
    if fee is not None:
        add("is_fee", fee)
    subs = _subjects(q)
    if subs:
        if len(subs) == 1:
            add("first_subject", subs[0])
        else:
            add_multi("first_subject", subs)
    # k12_category 默认同步课+同步拓展课
    conds.append({"field": "k12_category", "values": ["同步课", "同步拓展课"], "operator": "or"})
    if not conds:
        return None
    return {"query": {"and": conds}}


def _branch_slow(q: str):
    if not re.search(r"备课", q):
        return None
    return ("edu_slow_search_data_search", {"query": q})


def _branch_training_org(q: str):
    # 培训机构/一对一非课程检索 → fuzzy（星火教育高中一对一辅导课程）
    if not re.search(r"教育.{0,6}(培训|辅导|一对一)|(培训|辅导)课程|一对一", q):
        return None
    return ("edu_fuzzy_search", {"query": q})


def _branch_non_regular(q: str):
    # 期末/复习/押题/总结/真题/自制/手账/阅读专项…一律非正规课程资源 → fuzzy
    if not re.search(r"期末|复习|考试范围|押题|阅读专项|总结|真题|自制|手账|手工|怎么做|备考", q):
        return None
    return ("edu_fuzzy_search", {"query": q})


def _branch_tutoring(q: str):
    # 辅导（含 同步辅导/一对一/机构）/冲刺/精讲/知识点/易错 → fuzzy，即便有年级+科目
    if not re.search(r"辅导|一对一|冲刺|精讲|知识点|易错|教育.{0,6}培训", q):
        return None
    return ("edu_fuzzy_search", {"query": q})


def _branch_unstructured(q: str):
    # 非结构化 → fuzzy（特色课/抽象短语）直接返回 query=原话
    if _is_structured(q):
        return None
    return ("edu_fuzzy_search", {"query": q})


def _branch_edu_search(q: str):
    d = build_dsl(q)
    if d:
        return ("edu_search", d)
    return None


def _branch_edu_search_fallback(q: str):
    # 兜底：结构化但 DSL 组装失败 → fuzzy
    return ("edu_fuzzy_search", {"query": q})


RULE_SET = RuleSet(
    rules=[
        Rule(id="edu_slow", tool="edu_slow_search_data_search", priority=1,
             title="备课/慢速检索", explain="命中 备课 → 慢速检索工具",
             decide=_branch_slow),
        Rule(id="edu_training_org", tool="edu_fuzzy_search", priority=2,
             title="培训机构/一对一", explain="培训机构/辅导课程/一对一 等非课程检索 → fuzzy",
             decide=_branch_training_org),
        Rule(id="edu_non_regular", tool="edu_fuzzy_search", priority=3,
             title="非正规课程资源", explain="期末/复习/押题/总结/真题/自习 等 → fuzzy",
             decide=_branch_non_regular),
        Rule(id="edu_coaching", tool="edu_fuzzy_search", priority=4,
             title="辅导/冲刺/精讲", explain="辅导/冲刺/精讲/知识点/易错 → fuzzy，即便带年级+科目",
             decide=_branch_tutoring),
        Rule(id="edu_unstructured", tool="edu_fuzzy_search", priority=5,
             title="非结构化", explain="无年级/科目/版本/阶段/学期等(K12 结构化) → fuzzy(原话)",
             decide=_branch_unstructured),
        Rule(id="edu_structured", tool="edu_search", priority=6,
             title="K12 结构化", explain="有年级/科目等结构化槽位 → 组装 DSL 检索",
             decide=_branch_edu_search),
    ],
    default=Rule(id="edu_fuzzy_fallback", tool="edu_fuzzy_search", priority=100,
                 title="结构化兜底 fuzzy", explain="结构化但 DSL 组装失败 → fuzzy(原话)",
                 decide=_branch_edu_search_fallback),
)


def apply(query: str) -> tuple[str, dict | None] | None:
    if not query or not query.strip():
        return None
    sel = RULE_SET.select_with_rule(query)
    if sel is None:
        return None
    tool, params, rule = sel
    return tool, params, rule.id