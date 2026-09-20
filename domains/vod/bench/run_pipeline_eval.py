"""vod 新决策主轴（fewshot 缓存 + 单次 LLM）端到端评测。

走真实链路：`app.engine.run` → `Domain.pipeline`（而不是只测某一段），
因此结果包含 badcase 优先级、postproc、L3 兜底的真实影响。

用法：
  python domains/vod/bench/run_pipeline_eval.py                  # testset 266 条
  python domains/vod/bench/run_pipeline_eval.py --set holdout    # badcases 中不在 testset 的 63 条
  python domains/vod/bench/run_pipeline_eval.py --no-cache       # 禁用 fewshot 缓存，强制走 LLM
  python domains/vod/bench/run_pipeline_eval.py --show 15        # 打印前 15 条 miss
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.domain import load_domain  # noqa: E402
from app.engine import run as run_engine  # noqa: E402
from app.models import PredictRequest  # noqa: E402
from hcTools.domains.vod import textkey  # noqa: E402


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


def load_cases(which: str):
    ts = json.loads((ROOT / "domains" / "vod" / "testset.json").read_text(encoding="utf-8"))
    if which == "testset":
        return [{"query": r["query"], "expected_tool": r["expected_tool"],
                 "expected_params": r.get("expected_params") or {}} for r in ts["records"]]
    tsk = {textkey.normalize(r["query"]) for r in ts["records"]}
    bc = json.loads((ROOT / "domains" / "vod" / "badcases.json").read_text(encoding="utf-8"))
    return [{"query": e["raw"], "expected_tool": e["tool"], "expected_params": e.get("params") or {}}
            for e in bc["entries"] if textkey.normalize(e["raw"]) not in tsk]


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default="testset", choices=["testset", "holdout"])
    ap.add_argument("-n", type=int, default=None)
    ap.add_argument("--no-cache", action="store_true", help="禁用 fewshot 精确缓存（仍注入 BM25 样例）")
    ap.add_argument("--no-shots", action="store_true", help="禁用 BM25 fewshot 注入（仍可精确缓存）")
    ap.add_argument("--no-badcase", action="store_true", help="禁用 L2 badcase 层")
    ap.add_argument("--holdout", action="store_true",
                    help="真留出：待评条目从 fewshot 池中剔除后再评（衡量泛化，非缓存命中）")
    ap.add_argument("--stride", type=int, default=4,
                    help="--holdout 时的取样步长（默认 4，即每 4 条取 1 条）")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="以 JSON 输出汇总（stdout）")
    args = ap.parse_args()

    domain = load_domain("vod")
    # 两个开关互相独立：no_cache 只关「精确命中直出」，no_shots 只关「BM25 注入」。
    # 都关 = 纯 LLM 裸跑，用来量 fewshot 各层的真实贡献。
    if args.no_cache:
        domain._vod_no_cache = True
    if args.no_shots:
        domain._vod_no_shots = True
    if args.no_badcase:
        domain.badcase_lookup = None

    rows = load_cases(args.set)
    if args.holdout:
        rows = rows[:: max(1, args.stride)]
    if args.n:
        rows = rows[: args.n]

    # 真留出：把待评条目从 fewshot 池里挖掉，池子只留其余 testset。
    # 这才是「线上遇到库里没有的问法」的真实泛化口径（--no-cache 的池里仍含近邻，偏乐观）。
    if args.holdout:
        from hcTools.domains.vod.pipeline import ShotPool
        held = {r["query"] for r in rows}
        ts = json.loads((ROOT / "domains" / "vod" / "testset.json").read_text(encoding="utf-8"))
        pool_records = [r for r in ts["records"] if r["query"] not in held]
        domain._vod_pool = ShotPool(pool_records)
        domain._vod_no_cache = True   # 挖掉后本来也命中不了，显式关闭以防万一
        print(f"留出模式：池 {len(pool_records)} 条，待评 {len(rows)} 条（池中已剔除）")

    sem = asyncio.Semaphore(args.concurrency)

    async def one(r):
        async with sem:
            try:
                p = await run_engine(PredictRequest(query=r["query"], domain="vod"), domain)
                return r, p
            except Exception as exc:  # noqa: BLE001
                return r, None

    out = await asyncio.gather(*(one(r) for r in rows))

    st = Counter()
    src = Counter()
    misses = []
    for r, p in out:
        et, ep = r["expected_tool"], r["expected_params"]
        if p is None:
            st["err"] += 1
            misses.append({"query": r["query"], "err": "exception", "exp": (et, ep), "got": None})
            continue
        src[p.hit_source] += 1
        ok_t = p.tool == et
        ok_p = params_equal(p.params, ep)
        st["tool" if ok_t else "tool_miss"] += 1
        st["param" if ok_p else "param_miss"] += 1
        st["both" if (ok_t and ok_p) else "both_miss"] += 1
        if not (ok_t and ok_p):
            misses.append({"query": r["query"], "hit_source": p.hit_source, "ok_t": ok_t,
                           "exp": (et, ep), "got": (p.tool, p.params)})

    N = len(rows)
    tag = f"cache={'off' if args.no_cache else 'on'},shots={'off' if args.no_shots else 'on'}"

    if args.json:
        print(json.dumps({
            "set": args.set, "n": N, "no_cache": args.no_cache, "no_shots": args.no_shots,
            "no_badcase": args.no_badcase, "holdout": args.holdout,
            "score": {k: f"{st[k]}/{N} {st[k]/N:.1%}" for k in ("tool", "param", "both")},
            "errors": st["err"], "hit_source": dict(src.most_common()),
            "misses": misses if args.show else misses[: args.show],
        }, ensure_ascii=False, indent=2))
        return

    print(f"\n=== vod pipeline | {args.set} N={N} | {tag} ===")
    for k in ("tool", "param", "both"):
        print(f"{k:6s} {st[k]}/{N} {st[k]/N:.1%}")
    print(f"errors {st['err']}")
    print(f"hit_source: {dict(src.most_common())}")

    if args.show and misses:
        print(f"\n=== 前 {min(args.show, len(misses))} 条 miss ===")
        for m in misses[: args.show]:
            print(f"- {m['query']!r} src={m.get('hit_source')} ok_t={m.get('ok_t')}")
            print(f"    exp={json.dumps(m['exp'], ensure_ascii=False)}")
            print(f"    got={json.dumps(m['got'], ensure_ascii=False)}")

    suffix = (args.set + ("_holdout" if args.holdout else "")
              + ("_nocache" if args.no_cache else "")
              + ("_noshots" if args.no_shots else "")
              + ("_nobadcase" if args.no_badcase else ""))
    outp = ROOT / "domains" / "vod" / "bench" / f"pipeline_{suffix}.json"
    outp.write_text(json.dumps({"set": args.set, "n": N,
                                "no_cache": args.no_cache, "no_shots": args.no_shots,
                                "score": dict(st), "hit_source": dict(src), "misses": misses},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细写入 {outp}")


if __name__ == "__main__":
    asyncio.run(main())
