# vod 域 Bench

两条轨道，**并存**：

| | 脚本 | 跑的是 | 用途 |
|---|---|---|---|
| **主线** | `run_pipeline_eval.py` | 真实链路 `engine.run → Domain.pipeline` | ★ 当前架构的端到端评测 |
| 定标 | `eval_llm_onecall.py` | 只测单次 LLM 那一段（不经 engine） | 量 fewshot 注入的贡献 |
| 对照 | `run_bench.py` | 旧确定性层 `badcase → rules → fallback` | 历史对照，已与线上链路解耦 |

> 当前 vod 的决策主轴是 `domains/vod/pipeline.py`（fewshot 缓存直出 + 单次 LLM），
> 详见 [`../vod_design.md`](../vod_design.md)。

---

## 主线：`run_pipeline_eval.py`

对 `testset.json`（266 条 golden）走**真实链路**打分：结果包含 badcase 优先级、
postproc、L3 兜底的真实影响（而不是只测某一段）。

```bash
python domains/vod/bench/run_pipeline_eval.py                          # testset 266，默认
python domains/vod/bench/run_pipeline_eval.py --no-cache               # 关精确缓存（仍注入 shots）
python domains/vod/bench/run_pipeline_eval.py --no-shots               # 关 BM25 注入
python domains/vod/bench/run_pipeline_eval.py --no-badcase             # 关 L2 badcase
python domains/vod/bench/run_pipeline_eval.py --holdout --no-badcase   # ★真泛化
python domains/vod/bench/run_pipeline_eval.py --show 20                # 打印前 20 条 miss
python domains/vod/bench/run_pipeline_eval.py --json                   # 明细落 json
```

**两个开关互相独立**，别混：
- `--no-cache` 只关「归一 key 精确命中直出」，BM25 注入**仍开**；
- `--no-shots` 只关「BM25 fewshot 注入」；
- 都关 = 纯 LLM 裸跑，用来量各层贡献。

**`--holdout`** 才是泛化口径：按 `--stride`（默认 4）取样，并把待评条目**从池里挖掉**，
模拟「线上遇到库里没有的问法」。默认配置下 pool 就是评测集，**大部分条目会走缓存直出**，
测出来的是缓存命中率而非泛化能力。

### 指标

| 指标 | 含义 |
|------|------|
| `tool` | 预测 tool == expected_tool |
| `param` | 预测 params == expected_params（`and`/`values` 子列表 order-insensitive 归一） |
| `tool+param` | 两者同时命中（最严，产品口径） |

另附 `hit_source` 分布（`fewshot_cache` / `badcase` / `llm_onecall` / `fallback`），
用来判断「这一版改动到底影响了哪条路径」。

### 结果（2026-09-19，`testset.json` 266 条）

| 配置 | tool | tool+param |
|---|---|---|
| **默认（cache + shots）** | **100.0%** | **98.9%** |
| 真留出 67 条（池内剔除 + 关 badcase） | 95.5% | 80.6% |
| 关缓存、留 shots | 94.0% | 82.0% |
| 纯 LLM（都关） | 92.1% | 54.1% |

关缓存后 tool+param（82.0%）反而高于真留出（80.6%）：留出模式要从 199 条里检索，
比全池 266 条的 BM25 近邻稀疏，且关 badcase 时少了一层兜底。

`hit_source`（默认配置）：`fewshot_cache` 239 / `badcase` 27 / `llm_onecall` 0。
**默认配置下 LLM 一次都没跑。**

残余 3 条 miss 全是 `badcases.json` 与 `testset.json` 的数据冲突（同一条 query
两处 golden 不一致），非代码问题，需订正数据。

快照：`pipeline_testset.json`（默认）、`pipeline_testset_holdout_nobadcase.json`。

---

## 定标：`eval_llm_onecall.py`

只测「单次 LLM 出 tool+params」这一段，不经 engine（所以不受 badcase / 缓存影响）。

```bash
python domains/vod/bench/eval_llm_onecall.py --set testset    # 266 条
python domains/vod/bench/eval_llm_onecall.py --no-fewshot     # 关 BM25 注入
python domains/vod/bench/eval_llm_onecall.py -n 20            # 前 20 条
```

用途：量 **fewshot 注入的净收益**。注意这个脚本**不做工具下钻**（不经 pipeline），
模型倾向一律吐 `vod_search_all`，所以它的 tool 分数**系统性偏低**，只用来横向比
fewshot 开关，不要和 `run_pipeline_eval.py` 的数字对齐。

实测 266 条：

```
无 fewshot     tool  39.5%   tool+param 13.5%
top-7 BM25     tool  87.6%   tool+param 68.8%
                        ↑ +48.1pt      ↑ +55.3pt
```

---

## 对照：`run_bench.py`（旧确定性层）

跑 `badcase → rules.apply → fallback` 的确定性流水线。**已与线上链路解耦**——
vod 不再挂 `rule_select`，`rules.py`/`dsl.py` 仅作为 `fallback.py`/`postproc.py`
的依赖保留。留作历史对照与回归参考。

```bash
python domains/vod/bench/run_bench.py            # 汇总 + 前 10 条 diff
python domains/vod/bench/run_bench.py -n 40      # 前 40 条 diff
python domains/vod/bench/run_bench.py --all      # 全部 diff
python domains/vod/bench/run_bench.py --json     # JSON 汇总
```

快照：`result.latest.json`。

> 注意：跑脚本要求 `<root>/hcTools` 与其父目录都在 `sys.path` 上
> （`domains/vod/__init__` 会 `from app.domain import …`）。各脚本顶部已处理。
