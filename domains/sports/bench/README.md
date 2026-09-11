# sports（体育）域确定性三层兜底 Bench

三层兜底流水线：`badcase → rules.apply (L1) → fallback`，对
`testset.json`（186 条 golden）打分。

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

## 结果（2026-09-11，testset.json 186 条）
```
tool        186/186 100.0%
param       186/186 100.0%
tool+param  186/186 100.0%
```

run：`python domains/sports/bench/run_bench.py`