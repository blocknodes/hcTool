"""三层决策主轴的**通用**端到端评测器（各域共用）。

走真实链路 `app.engine.run → Domain.pipeline`，因此结果包含 badcase 优先级、
postproc、下钻、L3 兜底的真实影响（而不是只测某一段）。

打分口径与各域原有 `bench/run_bench.py` 保持一致：order-insensitive canonical 比较。

用法（以 children 为例）：
  python -m app.bench_pipeline children
  python -m app.bench_pipeline children --no-cache --no-shots   # 纯 LLM
  python -m app.bench_pipeline children --holdout --no-badcase  # 真泛化
  python -m app.bench_pipeline children --show 15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from .domain import load_domain  # noqa: E402
from .engine import run as run_engine  # noqa: E402
from .models import PredictRequest  # noqa: E402


def canonical(x):
    def key(c):
        return json.dumps(c, ensure_ascii=False, sort_keys=True)

    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k in {"and", "or"} and isinstance(v, list):
                out[k] = sorted((canonical(i) for i in v), key=key)
            elif k == "values" and isinstance(v, list):
                out[k] = sorted(v)
            else:
                out[k] = canonical(v)
        return out
    if isinstance(x, list):
        return sorted((canonical(i) for i in x), key=key)
    return x


def params_equal(a, b):
    return canonical(a) == canonical(b)


def load_cases(domain_key: str, which: str, textkey):
    base = ROOT / "domains" / domain_key
    ts = json.loads((base / "testset.json").read_text(encoding="utf-8"))
    records = [{"query": r["query"], "expected_tool": r["expected_tool"],
                "expected_params": r.get("expected_params") or {}} for r in ts["records"]]
    if which == "testset":
        return records
    tsk = {textkey.normalize(r["query"]) for r in records}
    bc = json.loads((base / "badcases.json").read_text(encoding="utf-8"))
    return [{"query": e["raw"], "expected_tool": e["tool"], "expected_params": e.get("params") or {}}
            for e in bc["entries"] if textkey.normalize(e["raw"]) not in tsk]


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("domain", help="域 key，如 children / education / music / vod")
    ap.add_argument("--set", default="testset", choices=["testset", "holdout"])
    ap.add_argument("-n", type=int, default=None)
    ap.add_argument("--no-cache", action="store_true", help="禁用 fewshot 精确缓存（仍注入 BM25 样例）")
    ap.add_argument("--no-shots", action="store_true", help="禁用 BM25 fewshot 注入（仍可精确缓存）")
    ap.add_argument("--no-badcase", action="store_true", help="禁用 L2 badcase 层")
    ap.add_argument("--holdout", action="store_true",
                    help="真留出：待评条目从 fewshot 池中剔除后再评（衡量泛化，非缓存命中）")
    ap.add_argument("--stride", type=int, default=4, help="--holdout 时的取样步长（默认 4）")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="以 JSON 输出汇总（stdout）")
    args = ap.parse_args()

    key = args.domain
    domain = load_domain(key)
    if domain.pipeline is None:
        print(f"域 {key} 未挂 pipeline（仍走旧的 select→fill 两段式）", file=sys.stderr)
        return 2

    from importlib import import_module

    textkey = import_module(f"domains.{key}.textkey")
    pipeline_mod = import_module(f"domains.{key}.pipeline")

    # 两个开关互相独立：no_cache 只关「精确命中直出」，no_shots 只关「BM25 注入」。
    # 都关 = 纯 LLM 裸跑，用来量 fewshot 各层的真实贡献。
    if args.no_cache:
        setattr(domain, f"_{key}_no_cache", True)
    if args.no_shots:
        setattr(domain, f"_{key}_no_shots", True)
    if args.no_badcase:
        domain.badcase_lookup = None

    rows = load_cases(key, args.set, textkey)
    if args.holdout:
        rows = rows[:: max(1, args.stride)]
    if args.n:
        rows = rows[: args.n]

    # 真留出：把待评条目从 fewshot 池里挖掉，池子只留其余 testset。
    if args.holdout:
        from .pipeline_kernel import ShotPool

        held = {r["query"] for r in rows}
        ts = json.loads((ROOT / "domains" / key / "testset.json").read_text(encoding="utf-8"))
        pool_records = [r for r in ts["records"] if r["query"] not in held]
        setattr(domain, f"_{key}_pool", ShotPool(pool_records, textkey, tag=key))
        setattr(domain, f"_{key}_no_cache", True)  # 挖掉后本来也命中不了，显式关闭以防万一
        print(f"留出模式：池 {len(pool_records)} 条，待评 {len(rows)} 条（池中已剔除）")

    sem = asyncio.Semaphore(args.concurrency)

    async def one(r):
        async with sem:
            try:
                p = await run_engine(PredictRequest(query=r["query"], domain=key), domain)
                return r, p
            except Exception as exc:  # noqa: BLE001
                return r, exc

    out = await asyncio.gather(*(one(r) for r in rows))

    st = Counter()
    src = Counter()
    misses = []
    for r, p in out:
        et, ep = r["expected_tool"], r["expected_params"]
        if not isinstance(p, object) or p is None or isinstance(p, Exception) or getattr(p, "error", "") and not p.tool:
            misses.append({"query": r["query"], "err": str(p)[:200], "exp": (et, ep), "got": None})
            continue
        src[p.hit_source] += 1
        gt, gp = p.tool, p.params
        ok_t, ok_p = gt == et, params_equal(gp, ep)
        ok_b = ok_t and ok_p
        st["tool"] += ok_t
        st["param"] += ok_p
        st["both"] += ok_b
        if not ok_b:
            misses.append({"query": r["query"], "hit_source": p.hit_source, "ok_t": ok_t,
                           "exp": [et, ep], "got": [gt, gp]})

    N = len(rows)

    def fmt(n):
        return f"{n}/{N} {n / N:.1%}" if N else "n/a"

    if args.json:
        print(json.dumps({"domain": key, "set": args.set, "n": N,
                          "score": {"tool": fmt(st["tool"]), "param": fmt(st["param"]),
                                    "both": fmt(st["both"])},
                          "hit_source": dict(src), "misses": misses},
                         ensure_ascii=False, indent=2))
        return 0

    flags = []
    if args.no_cache:
        flags.append("cache=off")
    if args.no_shots:
        flags.append("shots=off")
    print(f"\n=== {key} pipeline | {args.set} N={N} | {', '.join(flags) or 'cache=on,shots=on'} ===")
    print(f"tool   {fmt(st['tool'])}")
    print(f"param  {fmt(st['param'])}")
    print(f"both   {fmt(st['both'])}")
    print(f"errors {len([m for m in misses if m.get('got') is None])}")
    print(f"hit_source: {dict(src)}")
    if args.show:
        print(f"\n=== 前 {min(args.show, len(misses))} 条 miss ===")
        for m in misses[: args.show]:
            print(f"- {m['query']!r} src={m.get('hit_source')} ok_t={m.get('ok_t')}")
            print(f"    exp={json.dumps(m['exp'], ensure_ascii=False)}")
            print(f"    got={json.dumps(m['got'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
