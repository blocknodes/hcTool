"""音乐（music）域。

导出唯一 Domain 实例。契约见 app.domain.Domain。

决策主轴（与 vod 同构）：**fewshot 缓存直出 + 单次 LLM 出 tool&params**，
三层调度在 `app/pipeline_kernel.py`。本域不再挂 rule_select。

- tools：来自同目录 schema.json（9 个工具）。
- pipeline：本域整段决策（局限本目录，见 pipeline.py）。
- postprocess：LLM 生成参数的结构归一（缓存直出与 badcase 不经过它）。
- badcase_lookup / fallback：L2 精确覆盖 / L3 安全网，均在 pipeline 内调用。
- rules.py / dsl.py：不再参与决策，rules 仅作历史对照保留；dsl 仍被 fallback 依赖。
"""

from __future__ import annotations

from pathlib import Path

from app.domain import Domain, _load_schema_json

from .badcase import BadcaseStore
from .fallback import fallback as _fallback
from .pipeline import pipeline as _pipeline
from .postproc import normalize as _postproc

_dir = Path(__file__).resolve().parent

tools = _load_schema_json(_dir)

_store = BadcaseStore.load(_dir / "badcases.json")

domain: Domain = Domain(
    key="music",
    name="音乐",
    tools=tools,
    pipeline=_pipeline,
    postprocess=_postproc,
    badcase_lookup=_store.lookup,
    fallback=_fallback,
)
