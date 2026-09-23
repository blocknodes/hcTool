"""单次 LLM 出 tool+params 的准确率实验（vod 域）。

架构假设：fewshot 精确命中 → 直出；否则单次 LLM 调用输出 {"tool":..., "params":{...}}。
本脚本只测「LLM 那一段」的真实能力，用于定标（不作为回归门槛）。

数据集分两档：
  --set holdout   badcases 中不在 testset 的 63 条（真泛化，无自命中泄漏）★默认
  --set testset   testset.json 266 条（fewshot 池本身，会自命中，测的是 LLM 上界）

用法：
  python domains/vod/bench/eval_llm_onecall.py                 # holdout 63 条全量
  python domains/vod/bench/eval_llm_onecall.py --set testset   # 266 条
  python domains/vod/bench/eval_llm_onecall.py -n 20           # 只看 20 条
  python domains/vod/bench/eval_llm_onecall.py --no-fewshot    # 关掉 fewshot 注入
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

from app.domain import _load_schema_json  # noqa: E402
from app.llm import chat, _parse_json_block  # noqa: E402
from app.metadata import PURPOSE_ONECALL, build_llm_metadata  # noqa: E402
from hcTools.domains.vod import postproc, textkey  # noqa: E402


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


TOOLS = _load_schema_json(ROOT / "domains" / "vod")
TOOL_BRIEFS = "\n".join(
    f"- {t.name}：{' '.join(t.description.split())[:400]}" for t in TOOLS
)
SCHEMAS = "\n\n".join(
    f"### {t.name}\n{json.dumps(t.parameters, ensure_ascii=False)[:3000]}" for t in TOOLS
)

SYSTEM = f"""你是电视语音助手的意图解析器。读用户的话，输出该调用的工具名和参数。

候选工具：
{TOOL_BRIEFS}

各工具参数 JSON Schema：
{SCHEMAS}

规则：
- 只输出一个 JSON 对象，形如 {{"tool": "工具名", "params": {{...}}}}，不要任何解释。
- params 严格按所选工具的 schema 填写，只抽用户明确表达的信息，不臆造。
- 用户没提的可选字段一律不要出现在 params 里（不要填 null / 空串 / 空数组）。
- 有 retext 字段时原样填用户完整原话。
- 若某工具 golden 允许空参数（用户只说了作品名/栏目名而无任何筛选维度），params 输出 {{}}。
"""


def load_cases(which: str, limit: int | None):
    if which == "holdout":
        ts = json.loads((ROOT / "domains" / "vod" / "testset.json").read_text(encoding="utf-8"))
        tsk = {textkey.normalize(r["query"]) for r in ts["records"]}
        bc = json.loads((ROOT / "domains" / "vod" / "badcases.json").read_text(encoding="utf-8"))
        rows = [
            {"query": e["raw"], "expected_tool": e["tool"], "expected_params": e.get("params") or {}}
            for e in bc["entries"] if textkey.normalize(e["raw"]) not in tsk
        ]
    else:
        ts = json.loads((ROOT / "domains" / "vod" / "testset.json").read_text(encoding="utf-8"))
        rows = [{"query": r["query"], "expected_tool": r["expected_tool"],
                 "expected_params": r.get("expected_params") or {}} for r in ts["records"]]
    return rows[:limit] if limit else rows


def make_fewshot(pool_rows, n: int):
    """留一法 fewshot：池 = testset 全集，检索时排除与当前 query 归一 key 相同的样本。

    返回 (pick_fn, bank)。pick_fn(query) -> 该 query 的 top-n 样例（BM25）。
    """
    from app.examples import ExampleBank
    bank = ExampleBank([
        {"query": r["query"], "expected_tool": r["expected_tool"], "expected_params": r["expected_params"]}
        for r in pool_rows
    ])
    # ExampleBank 只存 query/tool，params 另存映射
    pmap = {r["query"]: r["expected_params"] for r in pool_rows}

    def pick(query: str) -> list[dict]:
        qs = bank.pick_tools(query, {r["expected_tool"] for r in pool_rows}, limit=n)
        return [{"query": q, "expected_tool": t, "expected_params": pmap.get(q, {})} for q, t in qs]

    return pick


async def one(query: str, fewshot: list[dict]) -> tuple[str, dict, str]:
    msgs = [{"role": "system", "content": SYSTEM}]
    if fewshot:
        shot_lines = "\n".join(
            f"- {r['query']} → {json.dumps({'tool': r['expected_tool'], 'params': r['expected_params']}, ensure_ascii=False)}"
            for r in fewshot
        )
        msgs.append({"role": "user", "content": f"已标注的同类样例：\n{shot_lines}\n\n用户的话：{query}\n只输出 JSON："})
    else:
        msgs.append({"role": "user", "content": f"用户的话：{query}\n只输出 JSON："})
    # stage=bench：hcProxy 里可把离线定标流量与线上 /api/predict 流量区分开
    msg = await chat(msgs, metadata=build_llm_metadata(
        purpose=PURPOSE_ONECALL, domain="vod", stage="bench"))
    raw = str(msg.get("content") or "")
    obj = _parse_json_block(raw)
    if not isinstance(obj, dict):
        return "", {}, f"PARSE_FAIL:{raw[:80]}"
    tool = obj.get("tool") or obj.get("tool_name") or ""
    params = obj.get("params")
    if params is None:
        params = {k: v for k, v in obj.items() if k not in {"tool", "tool_name"}}
    return tool, (params if isinstance(params, dict) else {}), ""


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default="holdout", choices=["holdout", "testset"])
    ap.add_argument("-n", type=int, default=None)
    ap.add_argument("--no-fewshot", action="store_true")
    ap.add_argument("--shots", type=int, default=7)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--show", type=int, default=8, help="打印前 N 条 miss")
    args = ap.parse_args()

    rows = load_cases(args.set, args.n)
    pool = load_cases("testset", None)
    pick = (lambda q: []) if args.no_fewshot else make_fewshot(pool, args.shots)

    print(f"数据集 {args.set}：{len(rows)} 条；fewshot：top-{args.shots} BM25"
          f"（{'关闭' if args.no_fewshot else '开启'}，排除 self）")

    sem = asyncio.Semaphore(args.concurrency)
    results = []

    async def run_one(r):
        async with sem:
            try:
                shots = pick(r["query"])
                return r, await one(r["query"], shots)
            except Exception as exc:  # noqa: BLE001
                return r, ("", {}, f"ERR:{type(exc).__name__}:{exc}")

    out = await asyncio.gather(*(run_one(r) for r in rows))

    st = Counter()
    misses = []
    for r, (tool, params, err) in out:
        et, ep = r["expected_tool"], r["expected_params"]
        ok_t = tool == et
        # 候选：LLM 原样 params；以及过 postproc 后
        ok_p_raw = params_equal(params, ep)
        try:
            pp = postproc.normalize(tool, dict(params))
        except Exception:  # noqa: BLE001
            pp = params
        ok_p_post = params_equal(pp, ep)
        st["tool" if ok_t else "tool_miss"] += 1
        st["p_raw" if ok_p_raw else "p_raw_miss"] += 1
        st["p_post" if ok_p_post else "p_post_miss"] += 1
        st["both_raw" if (ok_t and ok_p_raw) else "both_raw_miss"] += 1
        st["both_post" if (ok_t and ok_p_post) else "both_post_miss"] += 1
        if err:
            st["err"] += 1
        if not (ok_t and ok_p_post):
            misses.append({"query": r["query"], "exp": (et, ep), "got": (tool, params),
                           "post": pp, "err": err, "ok_t": ok_t})

    N = len(rows)
    def pct(k, kk):
        return f"{st[k]}/{N} {st[k]/N:.1%}"

    print(f"\n=== {args.set} N={N} ===")
    print(f"tool           {pct('tool','tool_miss')}")
    print(f"param (raw)    {pct('p_raw','p_raw_miss')}")
    print(f"param (+post)  {pct('p_post','p_post_miss')}")
    print(f"both  (raw)    {pct('both_raw','both_raw_miss')}")
    print(f"both  (+post)  {pct('both_post','both_post_miss')}")
    print(f"errors         {st['err']}")

    if args.show and misses:
        print(f"\n=== 前 {min(args.show, len(misses))} 条 miss ===")
        for m in misses[: args.show]:
            print(f"- {m['query']!r} ok_t={m['ok_t']} {m['err']}")
            print(f"    exp={json.dumps(m['exp'], ensure_ascii=False)}")
            print(f"    got={json.dumps(m['got'], ensure_ascii=False)}")

    out_json = ROOT / "domains" / "vod" / "bench" / f"llm_onecall_{args.set}.json"
    out_json.write_text(json.dumps({
        "set": args.set, "n": N, "fewshot": args.no_fewshot is False,
        "score": {k: st[k] for k in st}, "misses": misses,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细写入 {out_json}")


if __name__ == "__main__":
    asyncio.run(main())
