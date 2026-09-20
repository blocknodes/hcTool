# device 域 Bench

> **历史对照保留**（`run_bench.py` + `result.latest.json` 是重构前的规则版快照）。
> device 域现已接入共享三层主轴：决策在 `domains/device/pipeline.py`，
> 评测用 `python -m app.bench_pipeline device`。本目录仅用于验证重构未掉点。

## 结果对比（`testset.json` 1699 条）

| 通道 | tool | param | tool+param |
|---|---|---|---|
| 规则版（本目录，历史基线） | 1699/1699 100.0% | 1698/1699 99.9% | 1698/1699 99.9% |
| **pipeline（现行，cache on）** | **1699/1699 100.0%** | **1698/1699 99.9%** | **1698/1699 99.9%** |

逐条一致。唯一 miss 是 golden 自身矛盾（`退出播放` 在 testset 里出现两次，
golden 分别是 `停止`/`退出`），属结构性上限。

缓存覆盖 1693/1699（99.6%），其余 6 条由 badcase 覆盖，故 cache-on 通道不触达
LLM/规则层 —— 这也是为什么重构**不掉点**：

| 通道 | tool | param | tool+param |
|---|---|---|---|
| pipeline `--no-cache --no-shots --no-badcase`（真跑 LLM+规则） | 1699/1699 100.0% | 1698/1699 99.9% | 1698/1699 99.9% |
| pipeline `--holdout --stride 4`（池中剔除待评，真泛化） | 425/425 100.0% | 424/425 99.8% | 424/425 99.8% |
| LLM **裸判定，规则矫正关闭**（抽样 340 条） | 295/340 86.8% | 121/340 35.6% | 111/340 32.6% |

## 架构：为什么 device 的三层权重和 vod 型域不同

| | vod 型域（vod/children/education/music/sports） | device |
|---|---|---|
| params 形态 | 嵌套条件树（`query` 子树） | 扁平 5 槽 `{operation, object, value, device, location}` |
| 取值来源 | LLM 从原话**抽取**维度 | **封闭集**：208 条对象 canonical 拼写 + 确定性时间/数值解析 |
| 规则层角色 | 少量兜底 | 全量确定性链，实证 1699/1699 tool、1698/1699 param |
| LLM 贡献 | 主力（条件树只有它抽得出） | 泛化兜底（链未命中的新说法） |

于是 device 的分工是 **LLM 定工具、规则链接管槽位**：

```
L2 badcase（6 条）→ ① fewshot 精确缓存（1693 条，直出不过 postproc）
   → ② 单次 LLM 出 {tool, 5 槽}
        → fix_params：规则链命中 → 以规则为准（tool+槽位一并接管）
                      规则链未命中 → 保留 LLM 判定，仅用词表归一 object 拼写
        → postproc → ③ L3 兜底
```

规则链无条件接管，而不是像 vod 那样「LLM 先判、只有发现语言证据才反改」——
因为 device 的整份参数契约都是封闭集，规则链正是这份契约的显式表达；实测把
vod 的「LLM 先判」直接搬过来会**丢掉一堆 LLM 本就会错的槽位**（如 `亮度太暗了`
LLM 判 `solve_picture_sound_problem_control`，golden 是 `numeric_adjust` 提高亮度）。
LLM 仍是不可省的一层：链未命中时由它泛化，再由词表归一拼写。

### 反例记录：不要按「孤立分支精度」划线

曾据各规则分支**孤立**跑出的精度设过一张「反改工具白名单」，实测有害、已废弃：

| 分支 | 孤立精度 | 链上精度 |
|---|---|---|
| `_numeric` | 71.8% | **185/185** |
| `_power` | 29.8% | **25/25** |
| `_playback` | 98.5% | **331/331** |

孤立跑低，是因为脱离了优先级链、抢答了本该由更专的分支处理的句子（`_numeric`
抢 `3小时后关机`）。**任何按孤立精度划的线都会误伤，只能信链的端到端结果。**

### golden 参数形态（后处理必须精确复刻）

| 形态 | 条数 | 说明 |
|---|---|---|
| 5 槽全量（`location` 恒 `""`） | 1594 | 常规 |
| `{intent}` | 41 | `solve_picture_sound_problem_control` 独有 |
| `{}` 空参数 | 38 | **全部**是屏保 VIP 购买/开通/续费类 |
| 5 槽 + `date_time`（无 `value`） | 24 | 关机+相对时间 |
| 5 槽 + `date_time` + `value` | 2 | `自动关机时间设置` / `关机时间设置` → `date_time="定时"` |
| `device="电视"` | 21 | 全部是屏保族 + 原话含「电视」 |

`location` 全表无一条非空；`device` 仅上述 21 条非空。

## 用法

```bash
python -m app.bench_pipeline device                        # 现行主轴
python -m app.bench_pipeline device --no-cache --no-badcase  # 真跑 LLM+规则
python -m app.bench_pipeline device --holdout --stride 4     # 真泛化
python domains/device/bench/run_bench.py                     # 规则版历史对照
```
