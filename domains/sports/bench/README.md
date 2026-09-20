# sports（体育）域确定性三层兜底 Bench（历史对照）

> **已不参与线上决策。** sports 域现已改为与 vod 同构的三层架构
> （fewshot 缓存直出 + 单次 LLM 出 tool&params + 后处理矫正），决策主轴在
> `domains/sports/pipeline.py`，评测用 `python -m app.bench_pipeline sports`。
> 本目录的规则版 bench 仅作历史对照保留，用于验证重构未掉点。

规则版流水线：`badcase → rules.apply (L1) → fallback`，对 `testset.json` 打分。

## 结果对比（testset.json 482 条）

| 通道 | tool | param | tool+param |
|---|---|---|---|
| 规则版（本目录，历史基线） | 482/482 100.0% | 480/482 99.6% | 480/482 99.6% |
| **pipeline（现行）** | **482/482 100.0%** | **480/482 99.6%** | **480/482 99.6%** |

2 条残余 miss 全部是 golden 自身矛盾（同一 query 出现两次、golden 不同）：

| query | 两种 golden |
|---|---|
| `山地自行车视频` | `sport_name=自行车` vs `sport_name=山地自行车` |
| `英超后面的比赛` | `sport_game=英超` vs `+live_state=1` |

纯 LLM 通道（`--no-cache --no-badcase`）tool 96.9% / param 82.4%。

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