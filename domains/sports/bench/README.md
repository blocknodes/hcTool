# sports（体育）域三层兜底 Bench（历史对照）

> **已不参与线上决策。** 本域现已改为与 vod 同构的三层架构
> （fewshot 缓存直出 + 单次 LLM 出 tool&params + 后处理矫正），决策主轴在
> `domains/sports/pipeline.py`，评测用 `python -m app.bench_pipeline sports`。
> 本目录的规则版 bench 仅作历史对照保留，用于验证重构未掉点。

规则版流水线：`badcase → rules.apply (L1) → fallback`，对 `testset.json` 打分。

## 2026-09-21：同步飞书 0917 schema（新增 sports_match_forecast）

飞书「体育工具 0715」sheet `0917` 定义 9 个工具，hcTools 对应：

- `schema.json` → 9 工具（新增 forecast）；3 个卡工具按 sheet 补 `matchId`
  属性（golden 语义仍恒 `{}`，见 `pipeline._NO_PARAM_TOOLS`）。
- `rules.py` `apply` 新增 forecast 分支（信号：预测/谁能赢/能赢吗/胜率/胜负；
  **裸「X对Y」「vs」不触发**，仍归 match_search）。
- `pipeline.py` 决策序加 forecast（第 7 位），参数写法加 home/guest 双队口径。
- testset.json 482 → **490**（+8 条 forecast golden，已按 rules 校验确定性产出）。

## 结果对比（testset.json 490 条）

| 指标 | tool | param | tool+param |
|---|---|---|---|
| 规则版（历史基线） | 490/490 100.0% | 488/490 99.6% | 488/490 99.6% |
| **pipeline（现行）** | **490/490 100.0%** | **488/490 99.6%** | **488/490 99.6%** |

2 条纯 miss 全部是 golden 自设矛盾（同一 query 出现两次、golden 不同）：

| query | 两种 golden |
|---|---|
| `山地自行车视频` | `sport_name=自行车` vs `sport_name=山地自行车` |
| `英超后面的比赛` | `sport_game=英超` vs `+live_state=1` |

纯 LLM 通道（`--no-cache --no-badcase`）tool 96.1% / param 71.0%
（490 条含 8 条新 forecast 无 fewshot 支撑，属结构上限；cache+badcase 全命中）。

## 时间维度：LLM 只判结构，取值由 `fix_params` 确定性补全

32 条 golden 带 `sport_time` / `sport_time_range` / `round_offset`，取的是
**金标生成时的固定基准日**（2026-08-24 / 2026-09-11），LLM 推算不出。
故 `pipeline.py::fix_params` 复用 `rules` 的解析器（base 感知）补值，实测 32/32。
线上把 `rules.BASE_A/BASE_B` 换成当天即可。

> `dsl.py` 里的同名日期函数把 `_BASE` 写死了，**不要用**；以 `rules.py` 的为准。



## 指标
- `tool`：预测 tool == expected_tool
- `param`：预测 params == expected_params（order-insensitive canonical，
  `and`/`values` 子列表排序比较）
- `tool+param`：两者同时命中（最严，产品口径）

## 工具
schema.json 共 10 个工具，其中 5 个由规则覆盖：
- 嵌套 query 工具：`sports_match_search` / `sports_match_forecast` /
  `sports_match_reservation` / `sports_vod_search`
- 扁平参数工具：`sports_rank_search`({sport_game,sport_rank_type}) /
  `sports_team_search`({sport_team})

注：`sports_vod_search` 原 schema 缺失，已按 `sports_match_search` 的
QueryNode DSL 结构补齐（testset 中 34 条黄金用到）。

## 历史结果（2026-09-11，早期 testset 186 条）
```
tool        186/186 100.0%
param       186/186 100.0%
tool+param  186/186 100.0%
```

现行 testset 已扩到 482 条，规则版在此之上为 480/482（见上表）。

run：`python domains/sports/bench/run_bench.py`