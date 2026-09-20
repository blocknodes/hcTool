"""影视（vod）域。

导出唯一的 Domain 实例。内核只依赖本实例满足的契约；本域业务知识全部局限在本目录。

决策主轴（2026-09-19 改）：**fewshot 缓存直出 + 单次 LLM 出 tool&params**
    L2 badcase → ① fewshot 归一 key 精确命中直出 → ② 单次 LLM（BM25 top-8 注入）
    → ③ L3 fallback
由 `Domain.pipeline` 钩子接管（见 app/engine.py），不再走 select→fill 两段式。
详见 `pipeline.py`。

- tools：来自同目录 schema.json（7 个工具）。
- pipeline：本域整段决策（局限本目录）。
- postprocess：LLM 生成参数的 DSL 结构归一（缓存直出与 badcase 不经过它）。
- badcase_lookup / fallback：L2 精确覆盖 / L3 安全网，均在 pipeline 内调用。
- rules.py / dsl.py：不再参与决策，仅作为 fallback.py / postproc.py 的依赖保留。

加载前提：hcTools 在 sys.path 上（`uvicorn app.main:app` 或 `python` 从 hcTools 目录启动）。
"""

from __future__ import annotations

from pathlib import Path

from app.domain import Domain, _load_schema_json  # noqa: WPS461 复用内核加载

from .badcase import BadcaseStore  # L2 badcase 库（零泛化精确覆盖）
from .fallback import fallback as _fallback  # L3 有序兜底
from .pipeline import pipeline as _pipeline  # 本域决策主轴（fewshot 缓存 + 单次 LLM）
from .postproc import normalize as _postproc  # 本域 DSL 结构后处理（局限本目录）

_dir = Path(__file__).resolve().parent

tools = _load_schema_json(_dir)

_store = BadcaseStore.load(_dir / "badcases.json")

domain: Domain = Domain(
    key="vod",
    name="影视",
    tools=tools,
    pipeline=_pipeline,
    postprocess=_postproc,
    badcase_lookup=_store.lookup,
    fallback=_fallback,
)