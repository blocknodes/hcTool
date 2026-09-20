"""三层决策主轴的共享内核：fewshot 缓存直出 + 单次 LLM + 后处理矫正。

由 vod 域（`domains/vod/pipeline.py`）实装并验证后抽出。各域只需给出一份 `KernelSpec`
（工具清单怎么暴露、提示词怎么写、参数怎么归一、搜索族怎么下钻），内核负责调度。

三层（顺序即优先级）：

    L2 badcase      归一 key 精确命中 → 直出（**不过 postproc**）
    ① fewshot_cache 归一 key 精确命中 testset 池 → 直出（**不过 postproc**）
    ② llm_onecall   单次 LLM 出 {tool,params} → canon → retext 归一 → postproc
                    → 搜索族下钻 → 表外维度改判 → 直出
    ③ fallback      L3 安全网 → postproc（保证非空）

为什么两处精确命中**不过 postproc**：命中即「与 golden 同形」，必须原样返回。
postproc 会把 golden 的空参数 `{}` 填成 `{action,retext}`、把单元素 `and` 拍平——
这些在 golden 里是明确的形态，属语义性改写，只应作用于 LLM 生成的宽松参数。

hit_source：
  badcase        L2 精确覆盖
  fewshot_cache  fewshot 精确命中（归一 key）
  llm_onecall    单次 LLM 决策成功（含首次输出不合法后的重试成功）
  fallback       L3 兜底
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .llm import _parse_json_block, chat
from .models import PredictRequest, Prediction

logger = logging.getLogger("hcTools.pipeline")

# 条件树里除 field 之外允许出现在同一层的键（用于识别「紧凑叶子」）
LEAF_KEYS = ("value", "values", "operator", "from", "to")


# ---------- fewshot 池 ----------
class ShotPool:
    """testset.json 建池：归一 key → (tool, params)，并带 BM25 检索。

    `textkey` 由域注入（各域 textkey.py 目前同源，但保持显式依赖）。
    """

    def __init__(self, records: list[dict], textkey, tag: str = ""):
        from .examples import ExampleBank

        self._textkey = textkey
        self._tag = tag
        # 归一 key → 该 key 下的全部候选（同 key 可能有多条 golden，甚至互相冲突）
        self._by_key: dict[str, list[tuple[str, str, tuple[str, dict]]]] = {}
        self._rows: list[dict] = []
        for rec in records or []:
            q = rec.get("query")
            tool = rec.get("expected_tool")
            if not q or not tool:
                continue
            params = rec.get("expected_params") or {}
            key = textkey.normalize(q)
            self._by_key.setdefault(key, []).append((q, tool, params))
            self._rows.append({"query": q, "expected_tool": tool, "expected_params": params})

        # 同 key 不同 golden 的组：告警出来（数据问题，不静默）
        for key, cands in self._by_key.items():
            sig = {json.dumps([t, p], ensure_ascii=False, sort_keys=True) for _, t, p in cands}
            if len(sig) > 1:
                logger.warning("%s fewshot 池 key 冲突 %r：%d 种 golden，缓存将按原句消歧",
                               tag, key, len(sig))

        self._params_by_query = {r["query"]: r["expected_params"] for r in self._rows}
        self._bank = ExampleBank(self._rows) if self._rows else None
        self._tools = {r["expected_tool"] for r in self._rows}

    def exact(self, query: str) -> tuple[str, dict] | None:
        """归一 key 精确命中 → (tool, params)，否则 None。

        同 key 多条时**按原句消歧**（golden 里同 key 的写法常有标点差异，如
        「有没有少儿编程入门课」vs「…课？」）。能原句对上就返回该条；对不上则不返回
        （交给 LLM 从原话重建），避免缓存对同一 key 强行给一个答案、另一条必错。
        """
        cands = self._by_key.get(self._textkey.normalize(query))
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0][1], cands[0][2]
        for q, tool, params in cands:
            if q == query:
                return tool, params
        return None

    def pick(self, query: str, k: int) -> list[dict]:
        """BM25 top-K 相似样例（排除与当前 query 同 key 的样本，避免泄漏答案）。"""
        if self._bank is None:
            return []
        picked = self._bank.pick_tools(query, self._tools, limit=k, exclude_self=False)
        qkey = self._textkey.normalize(query)
        out = []
        for q, tool in picked:
            if self._textkey.normalize(q) == qkey:
                continue
            out.append({"query": q, "expected_tool": tool,
                        "expected_params": self._params_by_query.get(q, {})})
        return out[:k]

    def __len__(self) -> int:
        return len(self._rows)


# ---------- 条件树通用工具 ----------
def iter_fields(node, out: set[str]) -> set[str]:
    """递归收集条件树里出现的 field 名（含紧凑叶子）。"""
    if isinstance(node, dict):
        if node.get("field"):
            out.add(node["field"])
        else:
            out.update(k for k in node if isinstance(k, str))
        for v in node.values():
            iter_fields(v, out)
    elif isinstance(node, list):
        for x in node:
            iter_fields(x, out)
    return out


def make_canon_query(all_fields: set[str]) -> Callable[[Any], Any]:
    """按字段名全集识别「紧凑叶子」{"title":"X"} 并补成标准形态。

    gomodel 偶尔漏掉 `field` 键，直接写成 `{field: value}`。不修的话：
      ① 字段集下钻会漏判该字段 → 误落到更窄的工具；
      ② postproc 把 dict 当字符串处理会抛 TypeError。
    故必须在 postproc 与下钻**之前**做。
    """

    def canon(node):
        if isinstance(node, list):
            return [canon(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "field" in node or any(k in node for k in ("and", "or", "not")):
            return {k: canon(v) for k, v in node.items()}
        keys = [k for k in node if k not in LEAF_KEYS]
        if keys and all(k in all_fields for k in keys):
            rest = {k: v for k, v in node.items() if k in LEAF_KEYS}
            leaves = []
            for k, v in node.items():
                if k not in all_fields:
                    continue
                if isinstance(v, dict) and ("from" in v or "to" in v):
                    leaves.append({"field": k, **v})  # 紧凑范围：{rate:{from,to}} → field+from/to
                else:
                    leaves.append({"field": k, "value": v})
            if len(leaves) == 1:
                return {**leaves[0], **rest}
            return {"and": leaves} if not rest else {"and": [{**l, **rest} for l in leaves]}
        return {k: canon(v) for k, v in node.items()}

    return canon


# ---------- 域规格 ----------
@dataclass
class KernelSpec:
    """一个域接入内核所需的全部域知识。"""

    key: str                                    # 域 key，用于日志与开关标志
    textkey: Any                                # textkey 模块（normalize/strip_punct）
    system_prompt: Callable[[Any, list], str]   # (domain, 暴露的工具) -> system 文本
    norm_retext: Callable[[str], str]           # retext 落库前的轻量归一
    retext_tools: set[str] = field(default_factory=set)   # 需要 retext 的工具
    search_family: set[str] = field(default_factory=set)  # 「找内容」族（用于表外维度改判）
    exposed_tools: Callable[[Any], list] | None = None    # 暴露给 LLM 的工具（默认全部）
    normalize_tool: Callable[[str], str] | None = None    # 模型输出名归一（默认恒等）
    drill: Callable[[dict], str] | None = None            # 搜索族下钻（默认不下钻）
    umbrella: str = ""                                    # 下钻前的 umbrella 工具名
    all_fields: set[str] = field(default_factory=set)     # 条件树已知字段全集
    fuzzy_tool: str = ""                                  # 表外维度改判目标
    oor_words: tuple[str, ...] = ()                       # 表外维度指标词
    shot_k: int = 8
    pool_path: Path | None = None
    build_messages: Callable[[Any, str, list], list] | None = None  # 覆盖默认消息构造
    # 需要原始 query 的矫正（普通 postprocess 只拿得到 tool+params）。
    # 用于补全 LLM 推算不了的取值，如相对日期（「明天」→ 具体 yyyyMMdd）。
    # 签名 (query, tool, params) -> (tool, params)；在 postprocess **之前**、下钻之前执行
    # （postprocess 是结构归一，必须收尾，否则会被矫正步骤重新破坏）。
    fix_params: Callable[[str, str, dict], tuple[str, dict]] | None = None

    def expose(self, domain) -> list:
        return self.exposed_tools(domain) if self.exposed_tools else list(domain.tools)

    def norm_tool(self, tool: str) -> str:
        return self.normalize_tool(tool) if self.normalize_tool else tool

    def canon_query(self, node):
        return make_canon_query(self.all_fields)(node) if self.all_fields else node

    def is_oor(self, query: str) -> bool:
        return bool(self.oor_words) and any(w in query for w in self.oor_words)

    def build_msgs(self, domain, query: str, shots: list[dict]) -> list[dict]:
        if self.build_messages is not None:
            return self.build_messages(domain, query, shots)
        msgs = [{"role": "system", "content": self.system_prompt(domain, self.expose(domain))}]
        if shots:
            lines = "\n".join(
                f"- {s['query']} → {json.dumps({'tool': self.norm_tool(s['expected_tool']), 'params': s['expected_params']}, ensure_ascii=False)}"
                for s in shots
            )
            content = f"已标注的同类样例：\n{lines}\n\n用户的话：{query}\n只输出 JSON："
        else:
            content = f"用户的话：{query}\n只输出 JSON："
        msgs.append({"role": "user", "content": content})
        return msgs


def build_pipeline(spec: KernelSpec):
    """按 spec 生成该域的三层决策主轴（挂到 Domain.pipeline）。"""
    _pool: ShotPool | None = None
    _loaded = False

    def default_pool() -> ShotPool | None:
        nonlocal _pool, _loaded
        if _loaded:
            return _pool
        _loaded = True
        path = spec.pool_path
        if path is None:
            return None
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            _pool = ShotPool(data.get("records") or [], spec.textkey, tag=spec.key)
            logger.info("%s fewshot 池加载 %d 条：%s", spec.key, len(_pool), path)
        except Exception as exc:  # noqa: BLE001 池加载失败只退化为「无缓存、无 fewshot」
            logger.error("%s fewshot 池加载失败（%s）：%s", spec.key, path, exc)
            _pool = None
        return _pool

    def _post(domain, tool: str, params) -> dict:
        """域后处理：失败回退原始参数，不影响主流程。"""
        if not isinstance(params, dict):
            return {}
        if domain.postprocess is None:
            return params
        try:
            out = domain.postprocess(tool, params)
            return out if isinstance(out, dict) else params
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 后处理失败：%s", spec.key, exc)
            return params

    def _pred(domain, tool: str, params: dict, source: str) -> Prediction:
        logger.info("%s-PIPELINE source=%s tool=%s", spec.key.upper(), source, tool)
        return Prediction(domain=domain.name, tool=tool, params=params, hit_source=source)

    def _extract(msg: dict, valid: set[str]) -> tuple[str, dict] | None:
        """从模型输出里取 (tool, params)；tool 不合法则 None。"""
        name, params = "", None
        # 原生 tool_calls（本网关仅在 tool_choice 指定函数名时出现，防御性兼容）
        calls = msg.get("tool_calls") or []
        if calls:
            fn = calls[0].get("function") or {}
            name = fn.get("name", "")
            try:
                parsed = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                parsed = None
            params = parsed if isinstance(parsed, dict) else None
        if not name:
            obj = _parse_json_block(str(msg.get("content") or ""))
            if isinstance(obj, dict):
                name = obj.get("tool") or obj.get("tool_name") or ""
                raw = obj.get("params")
                if raw is None:
                    raw = {k: v for k, v in obj.items() if k not in {"tool", "tool_name"}}
                params = raw if isinstance(raw, dict) else None
        if name not in valid:
            return None
        return name, (params or {})

    async def pipeline(req: PredictRequest, domain) -> Prediction | None:
        query = req.query or ""
        # 显式挂空的 ShotPool 即「禁用缓存」（None 才回落到默认池）
        pool = getattr(domain, f"_{spec.key}_pool", None)
        if pool is None:
            pool = default_pool()
        # 缓存开关与 fewshot 注入是两回事：缓存关掉后仍可用池里的样例做 BM25 注入
        cache_on = not getattr(domain, f"_{spec.key}_no_cache", False)
        shots_on = not getattr(domain, f"_{spec.key}_no_shots", False)

        # ---- L2 badcase（最高优先，直出不过 postproc）----
        if domain.badcase_lookup is not None:
            try:
                hit = domain.badcase_lookup(query)
                if isinstance(hit, tuple) and len(hit) == 2 and hit[0] in domain.tools_by_name:
                    return _pred(domain, hit[0], hit[1] if isinstance(hit[1], dict) else {}, "badcase")
            except Exception as exc:  # noqa: BLE001
                logger.error("%s badcase 层异常：%s", spec.key, exc)

        # ---- ① fewshot 精确命中 → 直出（不过 postproc）----
        if pool is not None and cache_on:
            try:
                hit = pool.exact(query)
            except Exception as exc:  # noqa: BLE001
                logger.error("%s fewshot 命中异常：%s", spec.key, exc)
                hit = None
            if hit and hit[0] in domain.tools_by_name:
                return _pred(domain, hit[0], hit[1], "fewshot_cache")

        # ---- ② 单次 LLM：一次调用同时定 tool 与 params ----
        mixed = []
        if pool is not None and shots_on:
            try:
                mixed = pool.pick(query, spec.shot_k)
            except Exception as exc:  # noqa: BLE001
                logger.error("%s fewshot 检索异常：%s", spec.key, exc)

        # 校验集含被隐藏的窄工具：模型若输出它，归一成 umbrella 后仍算合法
        valid = {t.name for t in spec.expose(domain)}
        if spec.umbrella:
            valid.add(spec.umbrella)
        parsed = None
        for attempt in range(2):
            try:
                msg = await chat(spec.build_msgs(domain, query, mixed))
            except Exception as exc:  # noqa: BLE001 网关失败 → 落 L3
                logger.error("%s 单次 LLM 调用失败：%s", spec.key, exc)
                break
            parsed = _extract(msg, valid)
            if parsed is not None:
                break
            logger.warning("%s 单次 LLM 输出不合法（第 %d 次）：%r",
                           spec.key, attempt + 1, str(msg.get("content"))[:200])

        if parsed is not None:
            tool = spec.norm_tool(parsed[0])
            params = parsed[1]
            # retext 是 required 字段：模型漏填时用原话兜底。不交给 postproc ——
            # 它拿不到原始 query，setdefault 会补出空串。
            if tool in spec.retext_tools:
                params["retext"] = spec.norm_retext(params.get("retext") or query)
            if isinstance(params.get("query"), dict):
                params["query"] = spec.canon_query(params["query"])
            # 需要原话才能完成的矫正（如相对日期解析）排在 postproc **之前**：
            # postproc 负责结构归一，必须最后跑，否则矫正步骤可能重新造出
            # postproc 刚修掉的形状（如 sports 的裸数组 query）。
            if spec.fix_params is not None:
                try:
                    tool, params = spec.fix_params(query, tool, params)
                except Exception as exc:  # noqa: BLE001 矫正失败不阻断主流程
                    logger.error("%s 参数矫正失败：%s", spec.key, exc)
            params = _post(domain, tool, params)
            # 搜索族按字段集确定性下钻到 golden 契约的窄工具（LLM 只看 umbrella）
            if spec.drill is not None and spec.umbrella and tool == spec.umbrella:
                tool = spec.drill(params)
            # 表外维度兜底：LLM 仍照常决策，只有它落到搜索族、而话里又出现了结构化表达
            # 不了的指标名时，才在后处理阶段改判 fuzzy（整句原样交给 fuzzy，丢掉半截条件树）。
            # 放在下钻之后：下钻只决定窄工具，改判会把它整个覆盖掉。
            if spec.fuzzy_tool and tool in spec.search_family and spec.is_oor(query):
                logger.info("%s 表外维度改判 fuzzy：%s", spec.key, query)
                tool = spec.fuzzy_tool
                params = _post(domain, tool, {"retext": spec.norm_retext(query)})
            return _pred(domain, tool, params, "llm_onecall")

        # ---- ③ L3 兜底 ----
        if domain.fallback is not None:
            try:
                tool, params = domain.fallback(query)
                if tool in domain.tools_by_name:
                    if spec.fix_params is not None:
                        try:
                            tool, params = spec.fix_params(query, tool, params)
                        except Exception as exc:  # noqa: BLE001
                            logger.error("%s 兜底参数矫正失败：%s", spec.key, exc)
                    params = _post(domain, tool, params)
                    return _pred(domain, tool, params, "fallback")
            except Exception as exc:  # noqa: BLE001
                logger.error("%s 兜底异常：%s", spec.key, exc)
        return None

    pipeline.__name__ = f"{spec.key}_pipeline"
    pipeline.default_pool = default_pool  # 供 bench/自检取池
    return pipeline
