# music 域确定性三层兜底 Bench（历史对照）

> **已不参与线上决策。** music 域现已改为与 vod 同构的三层架构
> （fewshot 缓存直出 + 单次 LLM 出 tool&params + 后处理矫正），决策主轴在
> `domains/music/pipeline.py`，评测用 `python -m app.bench_pipeline music`。
> 本目录的规则版 bench 仅作历史对照保留，用于验证重构未掉点。

规则版流水线的回归基准。对 `testset.json` 跑确定性规则打分，
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
| `param` | 预测 params == expected_params（`song`/`singer`/`tag`/`ip`/`version`/`album`/`lyrics` 等子列表 order-insensitive 归一） |
| `tool+param` | 两者同时命中（最严，产品口径） |

## 工具

工具 9 个，判定顺序（具体→宽泛）：
`fan(榜/最新)` → `tvchannel` → `qqmusic` → `history` → `favorite` → `mv` → `ksong` → `recommend` → `song_search`。

`music_song_search` 的扁平槽位（singer/tag/version/ip/album/song/lyricist/composer/toplist/lyrics）
由词表 + 启发式收敛；歌名走词表免误报。

## 结果对比（`testset.json` 495 条）

| 通道 | tool | param | tool+param |
|---|---|---|---|
| 规则版（本目录，历史基线） | 495/495 100.0% | 491/495 99.2% | 491/495 99.2% |
| **pipeline（现行）** | **495/495 100.0%** | **492/495 99.4%** | **492/495 99.4%** |

pipeline 净增 1 条：`换个一路向北`（规则版把 retext 归一成「播放一路向北」，
pipeline 直出 golden 原句）。纯 LLM 通道（`--no-cache --no-badcase`）tool 仍达
~99%，说明工具判别不依赖缓存。

3 条残余 miss **全部是 golden 自身矛盾**（同一条 query 在 testset 里出现两次、
golden 不同），属结构性上限，无法从 query 区分：

| query | 两种 golden（retext） |
|---|---|
| `欧若拉的音乐` | `适合在婚礼仪式上放的歌` vs `欧若拉的音乐` |
| `换个一路向北` | `换个一路向北` vs `播放一路向北` |
| `换一个起风了` | `换一个起风了` vs `播放起风了` |

缓存对同 key 多 golden 的处理：**按原句消歧**，原句对不上则不返回、交给 LLM
从原话重建（见 `app/pipeline_kernel.py::ShotPool.exact`）。

快照见 [`result.latest.json`](result.latest.json)（规则版）。

## 用法

```bash
python domains/music/bench/run_bench.py                # 汇总 + 前 10 条 diff
python domains/music/bench/run_bench.py -n 40          # 前 40 条 diff
python domains/music/bench/run_bench.py --all          # 全部 diff
python domains/music/bench/run_bench.py --json         # JSON 汇总
python domains/music/bench/run_bench.py --json --all   # 全量 JSON（含全部 diff）
```