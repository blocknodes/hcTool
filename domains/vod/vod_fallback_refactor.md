# vod 域「三层兜底」重构 · 施工大纲

> 面向直接施工:含文件、函数签名、数据格式与分阶段验证。
> 基于现有代码:`app/engine.py::run`、`domains/vod/rules.py::apply`、
> `domains/vod/dsl.py::build_search_dsl`、`domains/vod/postproc.py::normalize`、`app/domain.py::Domain`。

## 重要前置事实

当前 `rules.apply` 最后一行**永远返回** `("vod_fuzzy_search", {...})`,从不返回 `None`。
这意味着 vod 域现在**根本没走到过 LLM 的 `select_tool`**——工具永远由规则选定,
LLM 只被用于 `fill_params`。所以"规则没命中"这个分支现在是死的。
本次重构的核心动作之一,就是让第 1 层**敢于返回"我不确定"(`None`)**,把不确定的交给
LLM 或第 3 层。

---

## 0. 目标 / 非目标

**目标**
- 把兜底拆成 3 个物理隔离、职责单一的层:泛化规则 / badcase 库 / 有序降级。
- 全链路可观测:每个预测标注 `hit_source`,三层收益可独立统计。
- "绝不返回无法处理":任何 query 都有确定性终极兜底。

**非目标**
- 不改内核对其它 7 个域的行为(改动收敛在 `domains/vod/` + 少量 `app/` 契约字段)。
- 不追求 badcase 层泛化(它本分就是零泛化的精确覆盖)。

---

## 1. 命中来源枚举 `hit_source`

贯穿全链路的唯一可观测维度:

| 值 | 含义 | 层 |
|---|---|---|
| `badcase` | 归一后精确命中 badcase 库,直出 | L2 |
| `general_rule` | 泛化规则给出 tool+params | L1 |
| `rule_llm_fill` | 规则定 tool,LLM 填参 | L1+LLM |
| `llm_select` | LLM 两段式自己选 tool+填参 | LLM |
| `fallback` | 规则/LLM 都没稳,走 search→search_all→fuzzy 降级 | L3 |

---

## 2. 总体决策流水线

改造 `app/engine.py::run`(vod 走此路径,其它域行为不变):

```
run(req, domain):
    q = req.query

    # ---- L2 badcase override（最高优先，允许覆盖一切）----
    if domain.badcase_lookup:
        hit = domain.badcase_lookup(q)          # -> (tool, params) | None
        if hit:
            return Pred(tool, params, source="badcase")

    # ---- L1 泛化规则 ----
    r = domain.rule_select(q) if domain.rule_select else None   # (tool, params|None) | None
    if r is not None:
        tool, params = r
        if params is not None:
            return Pred(tool, _post(tool, params), source="general_rule")
        # 工具确定、参数不定 → LLM 只填参
        filled, err = await fill_params(req, domain, tool)
        if not err and filled is not None:
            return Pred(tool, _post(tool, filled), source="rule_llm_fill")
        # 填参失败 → 落 L3

    # ---- LLM 两段式（开放决策点 A：是否保留）----
    else:
        tool, err = await select_tool(req, domain)
        if not err:
            filled, ferr = await fill_params(req, domain, tool)
            if not ferr and filled is not None:
                return Pred(tool, _post(tool, filled), source="llm_select")

    # ---- L3 有序兜底：保证非空 ----
    tool, params = domain_fallback(q)           # search -> search_all -> fuzzy
    return Pred(tool, _post(tool, params), source="fallback")
```

`_post(tool, params)` = 现有 `postproc.normalize`,对非 search/search_all 原样返回。

**关键语义**:`fallback` 是"没稳才落"的网,和"自信地判成 fuzzy"(L1 主动返回 fuzzy)
**必须区分**——前者 `source=fallback`,后者 `source=general_rule`。这样兜底率才是
有意义的健康指标。

---

## 3. 契约与数据结构改动（`app/`,最小侵入）

### 3.1 `app/models.py` — `Prediction` 加字段
```python
class Prediction(...):
    domain: str = ""
    tool: str | None = None
    params: dict = {}
    error: str = ""
    hit_source: str = ""      # 新增：badcase|general_rule|rule_llm_fill|llm_select|fallback
```
> `PredictResponse` 是否透出 `hit_source` 由你定(建议:透出但可关,便于线上排查)。

### 3.2 `app/domain.py` — `Domain` 加两个可选钩子
```python
@dataclass
class Domain:
    ...
    badcase_lookup: Callable | None = None   # (query) -> (tool, params) | None
    fallback: Callable | None = None         # (query) -> (tool, params)
```
其它域不设 → 行为完全不变(隔离原则不破)。

### 3.3 `app/engine.py::run` — 按 §2 伪码改
- 抽一个 `_pred(domain, tool, params, source)` 小工具统一构造 + 打点。
- `select_tool` 分支仅当 `rule_select` 返回 `None` 时进入(见前置事实)。

---

## 4. Layer 1：泛化规则重构（`domains/vod/`）

**动作 A — 剥离"背题"正则**,从 `rules.py` 移除写死实体,迁往 L2 badcase:
- `金秀贤、金智媛` / `刘德华、…`(3.5 规则)
- `辩论赛经典`(3.6 规则)
- `_STRUCT_DIM` 里的 `许三多`
- `dsl.py` 中仅为个别样例存在的词表项(需逐项审)

**动作 B — 保留并明确"泛化型"**:
- `_CAT_MAP` / `_AREA_MAP` / `_TAG_MAP`(口语→schema 取值映射)——保留
- 维度抽取(`_actor`/`_director`/`_release`/`_rate`… 的**语序型**正则)——保留
- `postproc` 的结构折叠/重排/去重/sort 推导——保留

**动作 C — 让 `apply` 敢返回 `None`**:
```python
def apply(query) -> tuple[str, dict | None] | None:
    ...
    # 原第 7) 行「其余一律 fuzzy」删除或改为：
    return None      # ← 交给 L3 兜底，而不是伪装成 general_rule 的 fuzzy
```
> 这是本次最关键的一处改动:**规则层只对有把握的返回,没把握就交棒**。

**判据(写进注释,供后续维护)**:一条规则若只对 ≤2 个具体标题/人名生效 → 不属于 L1,下沉 L2。

---

## 5. Layer 2：badcase 库（新增）

### 5.1 文件
```
domains/vod/
├── badcases.json        # 数据（可 review、可 diff）
├── badcase.py           # 加载 + 归一 + 精确匹配
└── textkey.py           # 归一 key 算法（与评测共用！）
```

### 5.2 数据格式 `badcases.json`
```json
{
  "version": 1,
  "entries": [
    {
      "raw": "胡歌演的的电影",
      "tool": "vod_search",
      "params": { "action": "search", "retext": "胡歌演的的电影",
                  "query": { "and": [ {"field":"actor","value":"胡歌"},
                                      {"field":"category","value":"电影"} ] } },
      "note": "单字『演』漏判到 fuzzy",
      "added": "2026-09-11", "owner": "dingbo"
    }
  ]
}
```
- key 用 `textkey.normalize(raw)` 在**加载时**计算,不手写。
- `note/added/owner` 强制填,用于 §9 治理与晋升审计。

### 5.3 归一 key 算法 `textkey.py`（命门,务必和评测同源）
```python
def normalize(text: str) -> str:
    s = text.strip().lower()
    s = to_halfwidth(s)                 # 全角→半角
    s = re.sub(r"\s+", "", s)           # 去所有空白
    s = strip_punct(s)                  # 去标点（保留中文字符/数字/字母）
    s = strip_polite(s)                 # 可选：去「请/帮我/麻烦」等无意义前缀
    return s
```
- **原则:宁紧勿松**。归一太松会把不同意图撞进同一 key → 直出错答案(这是 L2 唯一的致命风险)。
- 配单测锁定:一批 `(输入, 期望 key)`,防止归一函数被无意改动。

### 5.4 `badcase.py` API
```python
class BadcaseStore:
    def __init__(self, entries: list[dict]):
        self._map: dict[str, tuple[str, dict]] = {}
        for e in entries:
            k = textkey.normalize(e["raw"])
            if k in self._map:
                logger.warning("badcase 冲突 key=%s，后者覆盖", k)   # 冲突要告警
            self._map[k] = (e["tool"], e["params"])

    @classmethod
    def load(cls, path: Path) -> "BadcaseStore": ...

    def lookup(self, query: str) -> tuple[str, dict] | None:
        return self._map.get(textkey.normalize(query))
```
- 加载失败**不可拖垮域**:`try/except` → 空库 + 告警(遵循单域失败隔离)。
- 冲突 key 必须告警(说明两条 badcase 归一后同形)。

### 5.5 `__init__.py` 接线
```python
from .badcase import BadcaseStore
_store = BadcaseStore.load(_dir / "badcases.json")
domain = Domain(..., rule_select=_rule_apply, postprocess=_postproc,
                badcase_lookup=_store.lookup,
                fallback=_fallback)
```

---

## 6. Layer 3：有序兜底（新增 `domains/vod/fallback.py`）

```python
from . import dsl
from .rules import _ALL_SIGNAL, _ALL_DIM     # 复用「是否需要全库维度」判据

def fallback(query: str) -> tuple[str, dict]:
    """保证返回非空。search -> search_all -> fuzzy 置信度递减。"""
    d = dsl.build_search_dsl(query)
    if d:
        need_all = bool(_ALL_SIGNAL.search(query) or _ALL_DIM.search(query))
        return ("vod_search_all" if need_all else "vod_search"), d
    return "vod_fuzzy_search", {"query": query}
```
- 语义:能干净拼出 DSL 就 search;命中全库维度用 search_all;完全结构化不了才 fuzzy。
- fuzzy 是终极安全网(param=整句,永远可构造)。

---

## 7. 可观测性:打点

- `engine.run` 每条返回前:`logger.info("PREDICT source=%s tool=%s", source, tool)`。
- `app/main.py` 已有出入参日志,把 `hit_source` 加进 OUT 行 JSON。
- (可选)进程内计数器,`/api/health` 或新增 `/api/stats` 暴露各 `source` 累计占比,
  便于线上直接看兜底率。

---

## 8. 评测与指标拆分（先做这个,量化现状）

新增 `domains/vod/eval.py`(把散在 `/tmp` 的评测收回仓库):
- 复用 `textkey.normalize` + 顺序无关比较(`and`/`values` 集合化)。
- 输出**按工具分桶**的 tool 准确率 / tool+param 准确率;
- 输出**按 `hit_source` 分桶**的占比与准确率(badcase / general_rule / rule_llm_fill / llm_select / fallback)。

> 这张表是判断"规则真实收益"的唯一依据,也拆穿 fuzzy(310/445) 注水。
> **建议作为第一阶段先交付。**

---

## 9. badcase 晋升闭环（治理,防黑洞）

- 每条 badcase 带 `note`,定期(或脚本)聚类:**同一模式反复进库** = L1 缺规则的信号
  → 晋升为泛化规则,并从 `badcases.json` 删除对应条目。
- 评测报告里 `badcase` 命中数单列;该数持续上涨 = 泛化在退化的红灯。
- PR 规范:新增 badcase 必须填 `note/owner`,并在描述里说明"为何暂不做成泛化规则"。

---

## 10. 文件改动清单

| 文件 | 动作 |
|---|---|
| `app/models.py` | `Prediction` 加 `hit_source` |
| `app/domain.py` | `Domain` 加 `badcase_lookup` / `fallback` 钩子 |
| `app/engine.py` | `run` 按 §2 重排;加 `_pred` 打点 |
| `app/main.py` | OUT 日志带 `hit_source` |
| `domains/vod/rules.py` | 剥背题正则;`apply` 末尾改返回 `None` |
| `domains/vod/textkey.py` | 新增,归一 key(评测共用) |
| `domains/vod/badcase.py` | 新增,`BadcaseStore` |
| `domains/vod/badcases.json` | 新增,数据 |
| `domains/vod/fallback.py` | 新增,有序兜底 |
| `domains/vod/eval.py` | 新增,分桶评测(从 /tmp 收回) |
| `domains/vod/__init__.py` | 接线两个新钩子 |
| `tests/vod/` | 归一 key 单测 + 三层路由单测 |

---

## 11. 施工顺序（每阶段可独立验证、可回滚）

- **阶段 0｜量化现状**(不改逻辑):`eval.py` + `hit_source` 打点。跑一次全量,
  拿到按工具/按来源的基线表。← 先做
- **阶段 1｜契约**:`Prediction.hit_source`、`Domain` 两钩子。空实现,不改行为,
  全量回归应完全一致。
- **阶段 2｜L3 兜底**:`fallback.py` 接线;`rules.apply` 末尾 `return None`。
  全量回归,确认 fuzzy 从 `general_rule` 迁到 `fallback`,总准确率不降。
- **阶段 3｜L2 badcase**:`textkey.py`+`badcase.py`+`badcases.json`(先空);单测锁归一。
- **阶段 4｜迁移背题**:把 §4-动作A 的写死正则逐条搬进 `badcases.json`,
  同步删 `rules.py` 对应分支,每搬一条跑一次全量,准确率不降。
- **阶段 5｜治理**:晋升脚本 + PR 规范。

---

## 12. 风险与回归防护

| 风险 | 防护 |
|---|---|
| 归一太松,badcase 撞车直出错答案 | §5.3 宁紧勿松 + 冲突告警 + 归一单测 |
| 兜底掩盖 L1 缺陷 | `source=fallback` 单独统计,设阈值告警 |
| 改正则吞旧 case(历史踩坑) | 每阶段/每条 badcase 迁移都跑**全量**,准确率不降才合入 |
| LLM 路径成本(决策点 A) | 见 §13 |

---

## 13. 开放决策点（需拍板）

- **A. LLM 两段式是否保留?** 现状它对 vod 其实是死代码(见前置事实)。三个选项:
  1. 删掉,vod 纯"规则+兜底"(最快最省,但彻底放弃 LLM 泛化);
  2. 保留,`rules=None` 时先 LLM select+fill,失败再 L3(最泛化,有成本/延迟);
  3. 折中,LLM 仅用于 `fill_params`(工具永远规则/兜底定,参数难时问 LLM)。
  → 倾向 **2**,兼顾泛化与不崩;若线上延迟敏感选 **3**。

- **B. badcase 优先级**:是否允许 badcase 覆盖 general_rule(即放最前)?
  按"止血优先"默认放最前,需确认。

- **C. `hit_source` 是否对外透出**到 `/api/predict` 响应?建议透出(可配置开关)。
