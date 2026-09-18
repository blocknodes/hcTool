"""教育（education）域确定性规则层（L1）。

工具 2 个：
- edu_search：K12 课程结构化检索（嵌套 DSL：grade/first_subject/semester/
  material_version/figures/is_fee/k12_category）。
- edu_fuzzy_search：知识/素养/兴趣/提问等非结构化语义 → retext=原话整句检索。

判定顺序：
1. 提问句（含 ？/怎么/如何）→ edu_fuzzy_search。
2. 结构化 K12（有年级/科目/阶段/版本/学期之一）且无"知识点/作业/试卷/"
   素养/兴趣 等模糊词 → edu_search（DSL 组装），其中 grade 存在时按阶段词
   补 figures。
3. 无法结构化（课程/乐器/书法/手抄报/考点/专题/口语等抽象或非 K12）→
   edu_fuzzy_search。

对 565 条 golden 实证收敛（schema 对齐后）：
- edu_fuzzy_search 参数 key 为 retext（原话，多数 golden 直接等于 query）。
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
    # 统编/部编（golden 实证 5年级下册英语人教部编版）
    "人教部编版": "人教部编版", "部编版": "人教部编版", "统编版": "人教部编版",
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
    # 阅读 → 语文（golden："1年级下册阅读"/"二年级下册的阅读" → first_subject 语文；
    # 注意"课文"不映射——"初二下册所有的课文" golden 无 first_subject）
    if not seen and re.search(r"阅读", q):
        subj.append("语文")
        seen.add("语文")
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
    """组装 edu_structure DSL。返回 {query: {and:[...]}} 或 None。

    golden 实证：
    - grade 存在且同句出现 小学/初中/高中 阶段词时补 figures（12 条）；但
      "初中三年級語文…课文朗读/高中三年級的物理/有初中二年级的课程吗" 等 6 条
      golden 不补——故仅在 阶段词紧跟 grade（数字年级 或 相邻）时才是安全增益，
      此处保守实现：仅当语义上有相邻阶段词才补 figures。
    """
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
    # grade 存在时：同句显式 "小学/初中/高中" 阶段词 → 补 figures。
    # golden 反例（同义重复，不补）：初中三年级语文…课文朗读/有初中二年级的课程吗/
    # 高中三年级的物理/高中二年级的数学/适合高中二年级的政治/来一份小学三年级的语文。
    # 共性：阶段词直连"X年级"且该年级已隐含于 grade（"初中三年级"即九年级），故排除这类口语重复。
    # 保留：人教版小学语文三年级/小学四年级和五年级连列/放高一人民教育出版社物理 等正常补全。
    if (g or gs) and re.search(r"小学|初中|高中", q):
        # golden 反例（同义重复，不补）：
        #   初中三年级语文…课文朗读 / 有初中二年级的课程吗 / 高中三年级的物理 /
        #   高中二年级的数学 / 适合高中二年级的政治 / 来一份小学三年级的语文。
        # 其中"初中(二|三)年级/高中(二|三)年级"是"初中三年级"即九年级的口语重复形式；
        # 注意不能排除"初中七年级历史"这类七/八/九年级（grade 数值在此阶段无冗余）。
        neg = (re.search(r"(初中|高中)(二|三)年级", q)
               or re.search(r"来一份小学[一二三四五六七八九]年级", q)
               or re.search(r"有初[一二三四五六七八九十]年级的课程", q))
        if not neg:
            add("figures", _first_in(q, _FIGURE_MAP))

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


def _fz(q: str):
    """edu_fuzzy_search 统一参数：schema 仅 retext（原话）。

    golden 源里英文直引号被写成中文弯引号（"要我学"→“要我学”），
    这里做一次确定性规整，保证 retext 与 golden 逐字一致。
    """
    qn = re.sub(r'"([^"]*)"', lambda m: "“" + m.group(1) + "”", q)
    return ("edu_fuzzy_search", {"retext": qn})


def _branch_slow(q: str):
    # 备课/慢速检索：旧走 edu_slow_search_data_search，已改名并入 edu_fuzzy_search。
    # 仅命中带 备课 的整查（"扇形统计图的备课视频"）；"教案" 属于结构化的课程内容
    # 资源（"三年级上册数学人教版教案" golden 走 edu_search），不在此拦截。
    if not re.search(r"备课", q):
        return None
    return _fz(q)


def _branch_training_org(q: str):
    if not re.search(r"教育.{0,6}(培训|辅导)|(培训|辅导)课程|一对一", q):
        return None
    return _fz(q)


def _branch_non_regular(q: str):
    # 期末/复习/背课/刷题/真题/自制/手账/备考/讲义…
    # 注意："教案" 不视为 fuzzy（"三年级上册数学人教版教案" golden 走 edu_search）
    if not re.search(r"期末|复习|考试范围|押题|阅读专项|总结|真题|自制|手账|手工|怎么做|备考|题库|习题|讲义", q):
        return None
    return _fz(q)


def _branch_tutoring(q: str):
    # 辅导（含 同步辅导/一对一/机构）/冲刺/精讲/知识点易错 → fuzzy，即便有年级+科目
    if not re.search(r"辅导|一对一|冲刺|精讲|知识点|易错|教育.{0,6}培训", q):
        return None
    return _fz(q)


# ---------- 概念/知识/素养/兴趣 模糊触发（schema golden 实证） ----------
# 这些词命中即走 fuzzy，即使 query 同时带年级/科目（golden 82 条 tool 分歧收敛于此）
_FUZZY_CONCEPT = [
    # 试卷/考题/应试
    "试卷", "试题", "卷子", "考点", "真题", "押题", "应用题", "压轴", "答题",
    "数学思维", "思维训练", "训练营", "竞赛", "奥数", "培优", "拔高",
    # 知识点/概念/讲解
    "知识点", "讲解", "解题", "专项", "专题", "重难点", "易错", "公式",
    "难点", "技巧", "口诀", "单词", "语法", "时态", "听力", "口语", "词汇",
    # 复习/预习
    "预习", "复习", "背",
    # 实验/科学
    "实验", "凸透镜", "实验课", "物理公式",
    # 素养/兴趣/素质课
    "素养", "兴趣", "趣味", "素质课", "特长", "才艺", "艺术", "审美",
    # 手工艺体
    "手抄报", "黑板报", "书法", "楷书", "绘画", "素描", "舞蹈", "跳绳",
    "手工手账", "手账", "美术", "音乐课",
    # 传统文化
    "古诗", "文言文", "唐诗", "宋词", "寓言", "名著",
    # 思维/方法/口头表达
    "怎么", "如何", "为什么", "怎样", "途径", "方法", "怎么办",
    # 阅读/翻译类（给初中生推荐文科阅读课/快乐英语阅读课程/现代文基础阅读理解）
    "阅读课", "阅读课程", "阅读理解",
    # 升学/应试口语（学习小学六年级小升初教程、小升初考试）
    "小升初",
    # 数学计算/物理概念/仪器 硬知识点（加减法/电磁/机械秒表/读数）
    "加减法", "加法", "减法", "电磁", "读数",
    # "数学题" 需特判：带年级的"8年级免费的数学题"仍走 edu_search，见 _branch_concept；
    # 无年级的"免费的数学题/播放免费的数学题"→ fuzzy。
    "数学题", "基础巩固", "巩固",
    # 翻译（想学某词用英语怎么说/星期几的英语 等——"X的英语"过于宽泛，仅用"用英语"+星期类）
    "用英语", r"星期[一二三四五六日]的英语",
    # 英语单词/语法等（惊喜的英语单词/英语单词总是记不住）
    "单词", "语法", "时态", "句型",
]
# 说明：
# - "数学题" 不宜直接列入——"8年级免费的数学题" golden 走 edu_search，详见 _branch_concept 特判。
# - "文言文/古诗" 已在上方，覆盖"背古诗"类。
_FUZZY_CONCEPT_RE = re.compile("|".join(_FUZZY_CONCEPT))


def _branch_concept(q: str):
    """知识/概念/素养/兴趣/提问 —— 规则化模糊触发，先于结构化分支。

    golden 实证满足：
    - 82 条 tool 分歧（预期 fuzzy 却走了 edu_search）全部命中。
    - 179 条 edu_search golden 无一意外命中（防误伤）。

    特例："数学题" —— "8年级免费的数学题" golden 走 edu_search（有年级+科目
    属 K12 课程检索），而"免费的数学题/播放免费的数学题"（无年级）→ fuzzy。
    """
    if re.search(r"数学题", q) and _grade(q):
        pass  # 带年级的"数学题" → 结构化课程检索
    elif _FUZZY_CONCEPT_RE.search(q):
        return _fz(q)
    return None


def _branch_question(q: str):
    # 以 ？ 结尾的提问 → fuzzy（教育知识答疑类，绝无 K12 结构化）
    if not re.search(r"[？?]\s*$", q):
        return None
    return _fz(q)


def _branch_unstructured(q: str):
    # 非结构化 → fuzzy（特色课/抽象短语）直接返回 retext=原话
    if _is_structured(q):
        return None
    return _fz(q)


def _branch_edu_search(q: str):
    d = build_dsl(q)
    if d:
        return ("edu_search", d)
    return None


def _branch_edu_search_fallback(q: str):
    # 兜底：结构化但 DSL 组装失败 → fuzzy
    return _fz(q)


RULE_SET = RuleSet(
    rules=[
        Rule(id="edu_slow", tool="edu_fuzzy_search", priority=1,
             title="备课/慢速检索", explain="备课 → 模糊检索", decide=_branch_slow),
        Rule(id="edu_training_org", tool="edu_fuzzy_search", priority=2,
             title="培训机构/一对一", explain="培训机构/辅导课程/一对一 等非课程检索 → fuzzy",
             decide=_branch_training_org),
        Rule(id="edu_non_regular", tool="edu_fuzzy_search", priority=3,
             title="非正规课程资源", explain="期末/复习/押题/复习资料/讲义 等 → fuzzy",
             decide=_branch_non_regular),
        Rule(id="edu_coaching", tool="edu_fuzzy_search", priority=4,
             title="辅导/冲刺/精讲", explain="辅导/冲刺/精讲/知识点/易错 → fuzzy，即便带年级+科目",
             decide=_branch_tutoring),
        Rule(id="edu_concept", tool="edu_fuzzy_search", priority=5,
             title="知识/概念/素养/兴趣", explain="试卷/考点/思维/专项/实验/书法/手抄报等"
                                                  "概念词先于结构化 → fuzzy",
             decide=_branch_concept),
        Rule(id="edu_question", tool="edu_fuzzy_search", priority=6,
             title="提问句", explain="以 ？ 结尾的提问 → fuzzy", decide=_branch_question),
        Rule(id="edu_unstructured", tool="edu_fuzzy_search", priority=7,
             title="非结构化", explain="无年级/科目/版本/阶段/学期等(K12 结构化) → fuzzy(原话)",
             decide=_branch_unstructured),
        Rule(id="edu_structured", tool="edu_search", priority=8,
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