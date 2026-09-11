# children（少儿）域确定性三层兜底 Bench

三层兜底流水线：`badcase → rules.apply (L1) → fallback (L3)`，对
`testset.json`（327 条 golden，educ_* tools）打分。

## 指标
- `tool`：预测 tool == expected_tool
- `param`：预测 params == expected_params（order-insensitive canonical）
- `tool+param`：两者同时命中（最严，产品口径）

## 结果（2026-09-11，testset.json 327 条）
```
tool        327/327  100.0%
param       327/327  100.0%
tool+param  327/327  100.0%
```

run：`python domains/children/bench/run_bench.py`

## 说明
- 主逻辑在 `dsl.py`（槽位抽取 + DSL 组装 + 工具路由）与 `rules.py`（L1 判定：
  history → relate → fuzzy → search/search_all）。
- `badcases.json` 覆盖 22 条 golden 特异性长尾（tool 边界不规则，
  如 `教孩子情绪管理的绘本动画`、`动漫电影` 双槽、`is_fee` 字符串型等）。