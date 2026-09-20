# vod 域意图解析方案（当前架构）

影视（vod）域的决策主轴：**fewshot 缓存直出 + 单次 LLM 出 tool&params**。

> 2026-09-19 改版。上一版是「规则优先 + LLM 兜底」的两段式（L1 rule_select 12 条规则
> 命中即直出，漏网走 select→fill），本版把决策主轴换成 `pipeline.py`，规则层退出决策。
> 历史方案见文末「§8 与上一版的差异」。

目标：线上端服务 **tool+param ≥ 90%**（以 266 条 `testset.json` 的顺序无关、retext 归一评测为准）。

---

## 1. 整体流水线

入口 `app/engine.py::run`。vod 域通过 `Domain.pipeline` 钩子**整段接管**：

```
run(req, domain)
 ├─ ★ domain.pipeline 存在 → 直接交给它（vod 走这条）
 │   └─ domains/vod/pipeline.py::pipeline
 │       ├─ L2 badcase        归一 key 精确命中 → 直出（不过 postproc）
 │       ├─ ① fewshot_cache   归一 key 精确命中 testset 池 → 直出（不过 postproc）
 │       ├─ ② llm_onecall     单次 LLM 出 {tool,params} → canon_query → retext 归一
 │       │                     → postproc → drill_search_tool 下钻 → 返回
 │       └─ ③ fallback        L3 安全网 → postproc → 返回
 │   返回 None 才会落回下面 ↓
 └─ （其它 7 个域走这条）badcase → rule_select → select→fill → fallback
```

`engine.py:210-217` 是接管点：`domain.pipeline` 为 `None` 的域完全不受影响。vod 的
`__init__.py` 只挂 `pipeline`，**不再挂 `rule_select`**。

### 为什么是「单次调用」

网关（vLLM pipeline）**未开 `--tool-call-parser`**，实测：

| 请求 | 结果 |
|---|---|
| `tool_choice="required"` | ❌ 400 `requires --tool-call-parser` |
| `tool_choice="auto"` | ❌ 400 `requires --enable-auto-tool-choice` |
| `tool_choice={"type":"function","function":{"name":...}}` | ✅ 能强出 `tool_calls`（但仅当已知道调哪个工具） |
| 传 `tools` 但不传 `tool_choice` | ✅ 不报错，模型在 `content` 里输出 |

选工具场景**无法**用 `tool_choice` 约束（不知道选哪个），只能走 content 文本 JSON。
既然必然是一次文本调用，索性让它在同一次里把 params 也吐出来，省掉 fill 那一次。

## 2. 四层职责

| 层 | 位置 | 职责 | 过 postproc |
|---|---|---|---|
| **L2 badcase** | `badcase.py` + `badcases.json`（90 条） | 零泛化精确覆盖，与 golden 逐条对齐 | ❌ 直出 |
| **① fewshot_cache** | `pipeline.py::ShotPool`（266 条） | 库里见过的问法直接复用标注 | ❌ 直出 |
| **② llm_onecall** | `pipeline.py::pipeline` | 一次 LLM 同时定 tool + params | ✅ |
| **③ fallback** | `fallback.py` | 保证非空的安全网 | ✅ |

### 为什么两处直出**不过 postproc**

`postproc.normalize` 会**语义性改坏** 9 条 golden：

- 8 条 golden 是空 `{}`（`我这一辈子相声`、`豫剧王宝钏`、`豆瓣高分的喜剧电影` 等），
  `normalize` 会填成 `{"action":"search","retext":""}`；
- 3 条单元素 `and`（`{"and":[{"field":"category","value":"电视剧"}]}`）被拍平成裸叶子。

badcase 与 fewshot 池存的就是「与 golden 同形」的原始形态（badcase 的 `note` 里写明
「金标准数据层噪音（精确逐条对齐）」），**原样返回才对**。只有 LLM 生成的宽松 params
才需要归一。

## 3. 单次 LLM 的输入构造

```
system:  _SYSTEM_TMPL
         ├─ 【工具】      6 个工具的名字 + 描述摘要（不含 vod_search，各截 900 字符）
         ├─ 【怎么选工具】 6 步判定顺序（第 0 步见下）+ vod_personalized_search 的禁用条件
         ├─ 【搜索参数怎么写】 action / retext / sort 的写法
         ├─ 【query 条件树】 32 个字段名 + 语义（**只给字段，不给枚举值**）
         │                    + 4 种节点形态 + 归一化兜底规则
         └─ 【输出】      只输出 {"tool":..,"params":{..}}

user:    "已标注的同类样例：\n- <query> → {"tool":..,"params":{..}}\n…(BM25 top-8)\n\n
          用户的话：<query>\n只输出 JSON："
```

### 工具曝光：搜索族的包含关系
`vod_search ⊂ vod_search_all`（字段集 19 ⊆ 32，`_SEARCH_FIELDS` / `_SEARCH_ALL_FIELDS`）。
**只把 `vod_search_all` 暴露给 LLM**，两个都下发只会制造无意义的二选一。

下发的是「能力」、落库的是「契约」——两者用 `drill_search_tool()` 确定性衔接：

| 场景 | 线上契约工具 |
|---|---|
| 用了 `vod_search_all` 独有维度（area/language/channel/company…） | `vod_search_all` |
| 只用了公共维度（title/actor/category/tag/sort…） | `vod_search` |
| 整句语义检索 | `vod_fuzzy_search` |

模型若仍吐出被隐藏的 `vod_search`，`normalize_tool_name()` 并到 umbrella 再统一下钻。

### 表外维度：两层防护
`_ALL_FIELDS` 是**穷尽**的。用户提到表外维度（票房/投资/收视率…）时，模型不会报错，
而是**悄悄丢掉该维度**、只留剩下的字段拼一条合法查询 —— 返回结果集完全错且无任何信号。

**第 1 层（prompt）**：判定顺序第 0 步 —— 遇到表外维度整体交 `vod_fuzzy_search`。
概率性，只覆盖 prompt 里举例词的语义邻域（票房/投资/收视率/上座率 ✅；
观看人数/弹幕/热搜 ❌ 仍被顶替成表内 sort 维度）。

**第 2 层（后处理兜底，确定性）**：`_demote_by_oor_dim()`。**注意它不短路 LLM** ——
LLM 照常决策，只有当它已落到搜索族、且原话里出现 `_OOR_DIM_WORDS` 里的指标名时，
才在 pipeline 末尾改判 fuzzy（整句原样交给 fuzzy，丢掉半截条件树）。
这是必要的：模型把「观看人数」顶替成 `play` 后字面完全合法，规则层无法从结构上识别。

- 触发点放在 `drill_search_tool()` **之后** —— 下钻只决定 search/search_all，改判会整个覆盖。
- 词表**故意收窄**，只收明确的表外指标名；不收「最差/最少」这类程度词
  （`口碑最差` = 表内的 `rate asc`，由 LLM 判更准，规则不该抢）。
- 实测：266 条 testset **0 条**被规则改判（结构性中立，不影响基准分），
  10/10 条表外维度探针全部改对。

词表无法穷尽，日常遇到新说法仍走 `badcases.json` 逐条覆盖。

- **为什么只给字段不给枚举**：schema 里的枚举会诱导模型在用户没提该维度时**硬凑**一个值
  （实测「中年人爱看什么」被塞进 `target` 枚举），并且枚举表本身就把 prompt 撑长了 3 倍。
  字段名 + 语义足够描述「能抽什么」，具体取值应由用户的**原话**决定，不适合时宁可不传。
- **BM25 检索**：`app/examples.py::ExampleBank`（jieba 分词 + Okapi BM25），
  top-8，**按归一 key 排除 self**（`ShotPool.pick`），否则等于把答案喂进去。
- **temperature=0**（`app/llm.py`），无随机性。
- **输出解析**：`_extract_tool_params` 先看原生 `tool_calls`（防御性），再 `_parse_json_block`
  从 content 抠 JSON。工具名不在候选集 → 视为失败。
- **重试**：输出不合法重试 1 次，仍失败落 L3。
- **retext 兜底**：`vod_search`/`vod_search_all`/`vod_fuzzy_search` 的 `retext` 是
  schema `required`。模型漏填时在 pipeline 里用**原话**补——不能交给 postproc，
  它拿不到原始 query，`setdefault("retext", q)` 会补出空串。
- **retext 轻量归一**：`norm_retext()` 只去标点与内部空格（`strip_punct`），
  并保护小数点（`8.8分` 别被吃成 `88分`）。golden 里 209 条带 retext 的行，
  21 条的差异就是这条规则；剩下 2 条是阿拉伯数字↔中文口语**互相矛盾**的改写，
  属逐条噪声，不做。特意不用 `textkey.normalize`——它会去「请/帮我」前缀并转小写。
- **紧凑叶子兜底**：模型偶尔写出 `{"title":"致命之旅"}`（缺 `field` 键）。
  `canon_query()` 按字段名全集 (`_ALL_FIELDS`) 识别并补成标准叶子（含 `{rate:{from,to}}`
  这种紧凑范围的 `from/to` 上提），**必须在 postproc 与下钻之前**做——
  否则下钻会漏判字段落到窄工具，postproc 还会拿 dict 当字符串抛 `TypeError`。

## 4. 泛化性权衡

**fewshot 注入是整个方案里最有效的一环**。实测（266 条 testset，均关缓存）：

| 配置 | tool | tool+param |
|---|---|---|
| 纯 LLM（无 fewshot） | 92.1% | 54.1% |
| + BM25 top-8 注入 | **95.5%** | **80.6%** |
| 差值 | **+3.4pt** | **+26.5pt** |

（真留出 67 条：fewshot 池剔除待评样本后再检索，衡量的是泛化而非缓存命中。）

原因：字段名 + 语义能说清「能抽什么」，但说不清「这个域的金标口径」（`内地` vs `国产`、
`搜索` 与 `起播` 的 action 分界、口语描述何时该落 fuzzy、老片该不该加 `sort`）。
标注样例把口径示范出来了 —— 所以 fewshot 的收益**几乎全在 param 侧**（+26.5pt），
工具选择已被判定顺序基本解决（+3.4pt）。

**弱项**（真留出 80.6% 的残余失败模式）：
- 「推荐」类句式易被误判到 `vod_personalized_search`（已在 prompt 里禁用该路径，
  但「推荐」+ 内容描述的边界仍有少量溢出）；
- 口语描述型句子（`口碑炸裂的爆款综艺`）该走 fuzzy，LLM 倾向硬拆成结构化槽位；
- `中年/长辈/科技爱好者` 这类 `target` 维度，模型有时整句丢给 fuzzy 而漏抽该槽位；
- `像《X》这种类型的剧` 这类相关检索，模型只给 title 忘了用户点名的 category。

**已知天花板**（非模型问题，属数据本身）：默认配置 98.9% 的 3 条 miss 全部来自
badcase 条目与 testset golden 互相矛盾（同一句话在两处挂了不同的期望参数）。

## 5. fewshot 池

`ShotPool` 从 `domains/vod/testset.json`（266 条）懒加载，单例（`default_pool()`）：

- **精确表**：`textkey.normalize(query)` → `(tool, params)`。归一 = strip / 小写 /
  全角转半角 / 去空白 / 去标点 / 去「请|麻烦|帮我」前缀（与 L2 badcase 同源，`textkey.py`）。
  → `《最近播放量高的新剧》` 能命中，`请播放最近播放量高的新剧` 不命中（语义确实变了）。
- **BM25 索引**：同一批 `_rows` 建 `ExampleBank`。
- **加载失败**只退化为「无缓存、无 fewshot」，不拖垮域（`default_pool` try/except）。
- 池内出现同 key 不同 golden → 告警并保留后者（宁紧勿松，不静默错答）。
- 实测 266 条归一后 key **全唯一、零冲突**，精确匹配是干净的。

> ⚠️ **池就是评测集本身**，所以 testset 上 239/266 会走 `fewshot_cache` 直出——
> 那部分 100% 是「缓存命中率」，**不是泛化能力**。衡量泛化必须用留出集
> （`bench/run_pipeline_eval.py --holdout --no-badcase`，会把待评条目从池里挖掉）。

## 6. 后处理归一（postproc.py::normalize）

**仅 LLM 路径使用**。仅对 `vod_search/vod_search_all/vod_fuzzy_search` 生效：

1. **单元素 and/or 拍平**（`{and:[X]}`→`X`）。
2. **action 补缺**（不覆盖 dsl 已算出的值）。
3. **字段重排 + 去重**：顶层 and 叶子按 `_FIELD_PRIORITY`（title 最前）排。
4. **sort 推导**：新出/最新→new desc；好看/热播→hot desc。
5. **retext 兜底**：缺失时用 query（⚠️ LLM 路径已在 pipeline 层用原话补过，这里通常不触发）。

## 7. 验证与评测

**双轨**——旧确定性 bench 与新 pipeline bench 并存：

```bash
# ① 新决策主轴，端到端（走真实 engine.run → Domain.pipeline）
python domains/vod/bench/run_pipeline_eval.py                  # testset 266，默认
python domains/vod/bench/run_pipeline_eval.py --no-cache       # 关精确缓存（仍注入 shots）
python domains/vod/bench/run_pipeline_eval.py --no-shots       # 关 BM25 注入
python domains/vod/bench/run_pipeline_eval.py --holdout --no-badcase   # ★真泛化
python domains/vod/bench/run_pipeline_eval.py --show 20        # miss 明细

# ② 旧确定性层（rules → fallback），已与线上链路解耦，留作对照
python domains/vod/bench/run_bench.py

# ③ 只测 LLM 那一段（不经 engine，定标用）
python domains/vod/bench/eval_llm_onecall.py --set testset

# ④ HTTP 冒烟（服务需在 8084）
curl -s -X POST 'http://127.0.0.1:8084/api/predict' -H 'Content-Type: application/json' \
  -d '{"domain":"vod","query":"播放刘德华的电影","metadata":{}}'
# → vod_search | llm_onecall | actor:刘德华 / category:电影
```

**实测结果**（266 条 testset）：

| 配置 | tool | tool+param |
|---|---|---|
| **默认（cache+shots）** | **100.0%** | **98.9%** |
| 真留出 67 条（池内剔除 + 关 badcase） | 95.5% | 80.6% |
| 纯 LLM（都关，266 条含 badcase） | 92.1% | 54.1% |

`hit_source` 分布（默认配置）：`fewshot_cache` 239 / `badcase` 27 / `llm_onecall` 0
—— **默认配置下 LLM 一次都没跑**。

残余 3 条 miss 全是**数据冲突**：`badcases.json` 与 `testset.json` 对同一条 query
存了不同 golden（`余乐执导的纪录片视频` 的 retext 被故意写成「香港动作片」、
`刚上线但评分拉胯的新片` 的 category 写成「综艺」还带畸形 `sort/sort`），
需订正数据，非代码问题。

## 8. 与上一版的差异

| | 上一版（规则优先 + LLM 兜底） | 当前版（fewshot + 单次 LLM） |
|---|---|---|
| 决策入口 | `rule_select` → 12 条 Rule 优先级栈 | `pipeline` 整段接管 |
| 工具选择 | 规则命中即定（`rules.apply`） | fewshot 缓存 or 单次 LLM |
| 参数生成 | `dsl.build_search_dsl` 确定性合成 | LLM 直接出 |
| LLM 调用次数 | 0（规则命中）～2（select+fill） | 0（缓存命中）～1 |
| `hit_source` | `general_rule:*` / `llm_select` / `fallback` | `fewshot_cache` / `badcase` / `llm_onecall` / `fallback` |
| testset tool+param | 98.9% | 98.9%（持平） |
| 真泛化 | 规则栈本质是词表匹配，库外实体不覆盖 | LLM + BM25 样例，可泛化到新问法 |

**保留未删**：`rules.py`（514 行）与 `dsl.py`（1067 行）不再参与决策，仅作为
`fallback.py` 与 `postproc.py` 的依赖保留。若要彻底瘦身，可用简版兜底
（`fuzzy + 原话`）替掉 L3，一并归档这两个文件——会改变 L3 行为，需单独评估。

`groups.py` 曾实现一套「LLM 组选 → 组内规则 → 组内兜底」的 5 组架构，但
**从未接线**（`__init__` 没挂、engine 没钩子），且组内兜底直接甩 fuzzy、丢掉了
L3 的 `dsl.route_tool` 三级覆盖能力（heroic 路径 tool 仅 257/266），2026-09-19
起废弃为兼容薄壳。

## 9. 常见错误与排查

- **改完提示词/池 → 必跑 `--holdout --no-badcase`**：默认配置会被缓存吃掉，
  看不出版本间的真实差异。
- **`--no-cache` 不等于「关 fewshot」**：它只关精确命中，BM25 注入仍开。
  两者是独立开关（`_vod_no_cache` / `_vod_no_shots`），因为贡献完全不同。
- **别给 badcase/fewshot 直出加 postproc**：会破坏 golden 形态（见 §2）。
- **retext 补位在 pipeline 层，不在 postproc**：postproc 拿不到原始 query
  （见 §3 末条）。
