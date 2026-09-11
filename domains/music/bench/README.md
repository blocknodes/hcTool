# music 域确定性三层兜底 Bench

三层兜底流水线的回归基准。对 `testset.json`（207 条 golden）跑确定性规则打分，
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

## 结果（2026-09-11，`testset.json` 207 条）

```
tool        207/207  100.0%
param       205/207   99.0%
tool+param  205/207   99.0%
```

diff 分桶：
- `only-param-mismatch`  2  —— 均为 retext 数据噪声（golden retext ≠ query，无法确定性复现）

快照见 [`result.latest.json`](result.latest.json)。

## 用法

```bash
python domains/music/bench/run_bench.py                # 汇总 + 前 10 条 diff
python domains/music/bench/run_bench.py -n 40          # 前 40 条 diff
python domains/music/bench/run_bench.py --all          # 全部 diff
python domains/music/bench/run_bench.py --json         # JSON 汇总
python domains/music/bench/run_bench.py --json --all   # 全量 JSON（含全部 diff）
```