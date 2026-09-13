"""audio 域规则表审计：逐规则看命中 / 净贡献 / 误伤。

对 testset.json 里每条 golden，跑 RuleSet，统计每条规则：
  hits      这条规则命中条数
  good      命中且 (tool, params) 与 golden 一致（正贡献）
  harm      命中了但因它判错（本应由 default/后续规则得到正确 -> 这条规则改错了）
  idle      命中但结果与 golden 不符且也非"改错"（如 tool 对 param 错）

输出帮助判断："这条规则值不值得留 / 删了掉多少 / 关掉它（enabled=False）实验。"

用法：
  python domains/audio/rule_audit.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # hcTools/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import hcTools.domains.audio.badcase as badcase  # noqa: E402
import hcTools.domains.audio.fallback as fallback  # noqa: E402
import hcTools.domains.audio.rules as rules  # noqa: E402
from hcTools.domains.audio.bench.run_bench import canonical, params_equal  # noqa: E402


def _show_query(q: str) -> None:
    """打印单条 query 命中的规则（含命中子串）与最终决策。"""
    from hcTools.domains.audio import badcase as bc_m
    p = ROOT / "domains" / "audio" / "badcases.json"
    store = badcase.BadcaseStore.load(p) if p.exists() else badcase.BadcaseStore([])
    print(f"\nquery: {q!r}")
    bc_hit = store.lookup(q)
    if bc_hit is not None:
        print(f"  [L2 badcase] 命中 -> {bc_hit[0]} {bc_hit[1]}")
    # 逐规则打印（含命中子串），按 priority 顺序
    any_r = False
    for r, m in rules.RULE_SET.hits(q):
        any_r = True
        tag = "命中" if m else "未命中"
        print(f"  [L1 rule] {r.priority:>3} {r.id:<22} {tag}  matched={m!r}")
    sel = rules.RULE_SET.select_with_rule(q)
    if not any_r:
        print("  (无任何规则声明 match_re)")
    if sel is not None:
        tool, params, r = sel
        src = r.id if r is not rules.RULE_SET.default else "default"
        print(f"  -> 决策: {tool} {json.dumps(params, ensure_ascii=False)}  (rule={src})")
    else:
        print("  -> 决策: None (交给 fallback)")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--query", action="append", help="打印指定 query 命中哪个 rule（可多次）")
    ap.add_argument("--all", action="store_true", help="打印整份 testset 每条 query 的命中 rule")
    ap.add_argument("-n", "--limit", type=int, default=0, help="--all 时最多打印 N 条（0=全部）")
    args = ap.parse_args()

    if args.query:
        for q in args.query:
            _show_query(q)
        return

    if args.all:
        ts = ROOT / "domains" / "audio" / "testset.json"
        rows = json.loads(ts.read_text(encoding="utf-8"))["records"]
        print(f"{'query':<32}{'rule_id':<26}{'matched':<14}{'tool'}")
        print("-" * 100)
        for rec in rows[: args.limit] if args.limit else rows:
            q = rec["query"]
            sel = rules.RULE_SET.select_with_rule(q)
            if sel is None:
                print(f"{q:<32}{'fallback':<26}{'':<14}{'—'}")
                continue
            tool, params, rid = sel
            matched = rid.matched(q) if rid.match_re else ""
            print(f"{q:<32}{rid.id:<26}{matched[:12]:<14}{tool}")
        return
    main_audit()


def main_audit() -> None:
    ts = ROOT / "domains" / "audio" / "testset.json"
    data = json.loads(ts.read_text(encoding="utf-8"))
    records = data["records"]
    store = badcase.BadcaseStore([])
    bc_path = ROOT / "domains" / "audio" / "badcases.json"
    if bc_path.exists():
        store = badcase.BadcaseStore.load(bc_path)

    per: dict = defaultdict(lambda: {"hit": 0, "good": 0, "harm": 0, "idle": 0, "samp": []})

    def _bump(rid, ok, q):
        d = per[rid]
        d["hit"] += 1
        if ok:
            d["good"] += 1
        elif len(d["samp"]) < 5:
            d["samp"].append(q)
        # harm 用另一种口径：改特定 tool 的分
        return d

    for rec in records:
        q, et = rec["query"], rec["expected_tool"]
        ep = rec.get("expected_params") or {}

        bc = store.lookup(q)
        if bc is not None:
            hit_rule = "badcase"
            gt, gp = bc
        else:
            sel = rules.RULE_SET.select_with_rule(q)
            if sel is None:
                hit_rule = "fallback"
                gt, gp = fallback.fallback(q)
            else:
                gt, gp, r_rule = sel
                hit_rule = r_rule.id

        ok_t = gt == et
        ok_p = params_equal(gp, ep)
        d = _bump(hit_rule, ok_t and ok_p, q)
        if not (ok_t and ok_p) and hit_rule != "fallback":
            d["harm"] += 1

    print(f"=== audio 规则表审计（{len(records)} 条 golden）===")
    print(f"{'规则':<26}{'命中':>5}{'正确':>6}{'误伤':>6}  样例")
    for rid in sorted(per, key=lambda k: -per[k]["hit"]):
        d = per[rid]
        print(f"{rid:<26}{d['hit']:>5}{d['good']:>6}{d['harm']:>6}  {', '.join(d['samp'][:3])}")


if __name__ == "__main__":
    main()