"""children 域确定性三层兜底回归基准（bench）。

打分 testset.json（369 条 golden），跑确定性流水线：
    badcase → rules.apply → fallback

指标：tool / param / tool+param（joint，最严，order-insensitive canonical）。

用法：
  python domains/children/bench/run_bench.py
  python domains/children/bench/run_bench.py -n 20
  python domains/children/bench/run_bench.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from hcTools.domains.children import badcase, fallback, rules  # noqa: E402


def _j_key(c):
    return json.dumps(c, ensure_ascii=False, sort_keys=True)


def canonical(x):
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            out[k] = canonical(v)
        return out
    if isinstance(x, list):
        return sorted((canonical(i) for i in x), key=_j_key)
    return x


def params_equal(a, b):
    return canonical(a) == canonical(b)


_BAD = badcase.BadcaseStore([])


def load_badcases():
    global _BAD
    p = ROOT / "domains" / "children" / "badcases.json"
    _BAD = badcase.BadcaseStore.load(p) if p.exists() else badcase.BadcaseStore([])


def run_det(query):
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
            continue
        stats["diff"] += 1
        kind = "only-param-mismatch" if (ok_t and not ok_p) else "tool-divergence"
        buckets[kind] += 1
        rows.append({"query": q, "expected_tool": et, "expected_params": ep,
                     "got_tool": gt, "got_params": gp, "kind": kind})
    return stats, buckets, rows


def _fmt(num, den):
    return f"{num}/{den} {num / den:.1%}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    ts = ROOT / "domains" / "children" / "testset.json"
    if not ts.exists():
        print(f"评测集不存在：{ts}")
        sys.exit(1)
    data = json.loads(ts.read_text(encoding="utf-8"))
    records = data["records"]
    N = len(records)

    load_badcases()
    stats, _, rows = compute(records)

    if args.json:
        print(json.dumps({
            "domain": data.get("domain"), "count": N,
            "score": {
                "tool": _fmt(stats["tool"], N),
                "param": _fmt(stats["param"], N),
                "tool+param": _fmt(stats["both"], N),
            },
            "diffs": rows if args.all else rows[: args.n],
        }, ensure_ascii=False, indent=2))
        return

    print(f"tool        {_fmt(stats['tool'], N)}")
    print(f"param       {_fmt(stats['param'], N)}")
    print(f"tool+param  {_fmt(stats['both'], N)}")
    lim = len(rows) if args.all else args.n
    if lim:
        print(f"\n=== 前 {min(lim, len(rows))} 条 diff ===")
        for r in rows[:lim]:
            print(f"- {r['query']!r}  [{r['kind']}]\n"
                  f"    exp=({r['expected_tool']},{r['expected_params']})\n"
                  f"    got=({r['got_tool']},{r['got_params']})")


if __name__ == "__main__":
    main()