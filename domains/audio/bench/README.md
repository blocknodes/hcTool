# audio（有声）域确定性三层兜底 Bench

三层兜底流水线：`badcase → rules.apply (L1) → fallback (L3)`，对
`testset.json`（369 条 golden）打分。

## 指标
- `tool`：预测 tool == expected_tool
- `param`：预测 params == expected_params（order-insensitive）
- `tool+param`：两者同时命中（最严，产品口径）

## 结果（2026-09-11，testset.json 369 条）
```
tool        369/369  100.0%
param       369/369  100.0%
tool+param  369/369  100.0%
```

run：`python domains/audio/bench/run_bench.py`
