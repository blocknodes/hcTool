# vod 域确定性三层兜底 Bench

三层兜底重构的回归基准。对 `testset.json`（445 条 golden）跑确定性流水线打分，
判断规则/badcase/兜底的真实收益，并暴露 any 层的漏判。

## 判定流水线

```
badcase → rules.apply (L1) → fallback (L3)
```

每层优先级：badcase（L2，最严）→ 规则 → 兜底。

## 指标

| 指标 | 含义 |
|------|------|
| `tool` | 预测 tool == expected_tool |
| `param` | 预测 params == expected_params（`and`/`values` 子列表 order-insensitive 归一） |
| `tool+param` | 两者同时命中（最严，产品口径） |

## 结果（2026-09-11，`testset.json` 445 条）

```
tool        440/445  98.9%
param       432/445  97.1%
tool+param  432/445  97.1%
```

diff 分桶：
- `only-param-mismatch`  8  —— tool 对、param 差
- `fuzzy-involved`       4  —— 语义模糊前置与 golden 分歧
- `tool-divergence`      1  —— tool 判错

快照见 [`result.latest.json`](result.latest.json)。

## 用法

```bash
python domains/vod/bench/run_bench.py                # 汇总 + 前 10 条 diff
python domains/vod/bench/run_bench.py -n 40          # 前 40 条 diff
python domains/vod/bench/run_bench.py --all          # 全部 diff
python domains/vod/bench/run_bench.py --json         # JSON 汇总
python domains/vod/bench/run_bench.py --json --all   # 全量 JSON（含全部 diff）
```

> 注意：跑脚本要求 `<root>/hcTools` 与其父目录都在 `sys.path` 上
> （`domains/vod/__init__` 会 `from app.domain import …`，且以 `from hcTools…` 导入）。
> 脚本 `run_bench.py` 顶部已处理二者。

## 三层阀

- L1 `rules.apply`：确定性规则；末尾 `return None` 交给 L3，不伪装成 fuzzy。
- L2 `badcases.json`：归一 key（`textkey.normalize`）精确命中，最高优先级。
- L3 `fallback`：语义表达型 fuzzy 前置 → `dsl.route_tool` 三级覆盖判定
  （search → search_all → fuzzy）。