"""vod 域确定性三层兜底回归基准（bench）。

对 `testset.json`（445 条 golden）打分，跑确定性流水线：
    badcase → rules.apply → fallback

指标：
  tool        = 预测 tool == expected_tool
  param       = 预测 params（order-insensitive canonical）== expected_params
  tool+param  = tool 与 param 同时命中（joint，最严）

用法：
  python domains/vod/bench/run_bench.py        # 汇总 + 前 10 条 diff
  python domains/vod/bench/run_bench.py --n 40 # 明细条数
  python domains/vod/bench/run_bench.py --json # 输出 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# 包根 = ……/hcTools（hcTools 自身 + 其父目录都要在 path 上，
# hcTools/domains/vod/__init__ 会 `from app.domain import …`，app 也在 hcTools 目录内）
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))          # hcTools/  → from app import … 与 from hcTools… 都能解析
sys.path.insert(0, str(ROOT.parent))   # 上级     → 以包名 from hcTools… 导入

from hcTools.domains.vod import badcase, fallback, rules  # noqa: E402


def _j_key(c):
    return json.dumps(c, ensure_ascii=False, sort_keys=True)


def canonical(x):
    """DSL query 归一：and / values 子列表按内容排序（order-insensitive）。"""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k == "and" and isinstance(v, list):
                out[k] = sorted((canonical(i) for i in v), key=_node_key)
            elif k == "values" and isinstance(v, list):
                out[k] = sorted(v)
            else:
                out[k] = canonical(v)
        return out
    if isinstance(x, list):
        return sorted((canonical(i) for i in x), key=_j_key)
    return x


def _node_key(c):
    return _j_key(c) if isinstance(c, (dict, list)) else str(c)


def params_equal(a, b):
    return canonical(a) == canonical(b)


_BAD = badcase.BadcaseStore([])


def load_badcases():
    global _BAD
    p = ROOT / "domains" / "vod" / "badcases.json"
    _BAD = badcase.BadcaseStore.load(p) if p.exists() else badcase.BadcaseStore([])


def run_det(query):
    """badcase → rules.apply → fallback，返回 (tool, params)。"""
    hit = _BAD.lookup(query)
    if hit is not None:
        return hit
    r = rules.apply(query)
    if r is not None:
        return r[0], r[1] or {}
    return fallback.fallback(query)


def compute(records):
    stats = {"tool": 0, "param": 0, "both": 0, "diff": 0}
    buckets = Counter()
    rows = []
    for rec in records:
        q = rec["query"]
        et, ep = rec["expected_tool"], rec.get("expected_params") or {}
        gt, gp = run_det(q)
        ok_t, ok_p = gt == et, params_equal(gp, ep)
        ok_b = ok_t and ok_p
        stats["tool"] += ok_t
        stats["param"] += ok_p
        stats["both"] += ok_b
        if ok_b:
            buckets["pass"] += 1
            continue
        stats["diff"] += 1
        if ok_t and not ok_p:
            kind = "only-param-mismatch"
        elif et == "vod_fuzzy_search" or gt == "vod_fuzzy_search":
            kind = "fuzzy-involved"
        else:
            kind = "tool-divergence"
        buckets[kind] += 1
        rows.append({"query": q, "expected_tool": et, "expected_params": ep,
                     "got_tool": gt, "got_params": gp, "kind": kind})
    return stats, buckets, rows


def _fmt(num, den):
    return f"{num}/{den} {num / den:.1%}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    ap.add_argument("-n", type=int, default=10, help="diff 明细条数")
    ap.add_argument("--all", action="store_true", help="输出全部 diff 明细")
    args = ap.parse_args()

    ts = ROOT / "domains" / "vod" / "testset.json"
    if not ts.exists():
        print(f"评测集不存在：{ts}")
        sys.exit(1)
    data = json.loads(ts.read_text(encoding="utf-8"))
    records = data["records"]
    N = len(records)

    load_badcases()
    stats, buckets, rows = compute(records)

    if args.json:
        print(json.dumps({
            "domain": data.get("domain"), "count": N,
            "score": {
                "tool": _fmt(stats["tool"], N),
                "param": _fmt(stats["param"], N),
                "tool+param": _fmt(stats["both"], N),
            },
            "diff_buckets": dict(buckets),
            "diffs": rows if args.all else rows[: args.n],
        }, ensure_ascii=False, indent=2))
        return

    print(f"tool        {_fmt(stats['tool'], N)}")
    print(f"param       {_fmt(stats['param'], N)}")
    print(f"tool+param  {_fmt(stats['both'], N)}")
    print(f"diff-buckets: {dict(buckets)}")
    lim = len(rows) if args.all else args.n
    if lim:
        print(f"\n=== 前 {min(lim, len(rows))} 条 diff ===")
        for r in rows[:lim]:
            print(f"- {r['query']!r}  [{r['kind']}]\n"
                  f"    exp=({r['expected_tool']},{r['expected_params']})\n"
                  f"    got=({r['got_tool']},{r['got_params']})")


if __name__ == "__main__":
    main()