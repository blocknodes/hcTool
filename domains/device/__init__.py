"""设备（device）域。

导出唯一 Domain 实例。契约见 app.domain.Domain。
- tools：来自同目录 schema.json（17 个设备工具）。
- 无嵌套 DSL，params 扁平 5 槽。

决策走三层主轴（`pipeline.py`）：fewshot 缓存直出 + 单次 LLM 定工具 + 规则槽位后处理。
`rule_select` 仍保留 —— 它是 `pipeline.fix_params` 的规则来源，也供非主轴调用方复用。

加载前提：hcTools 在 sys.path 上。
"""
from __future__ import annotations

from pathlib import Path

from app.domain import Domain, _load_schema_json

from .rules import apply as _rule_apply
from .postproc import normalize as _postproc
from .badcase import BadcaseStore
from .fallback import fallback as _fallback
from .pipeline import pipeline as _pipeline

_dir = Path(__file__).resolve().parent

tools = _load_schema_json(_dir)

_store = BadcaseStore.load(_dir / "badcases.json")

domain: Domain = Domain(
    key="device",
    name="设备",
    tools=tools,
    example_bank=None,
    pipeline=_pipeline,
    rule_select=_rule_apply,
    postprocess=_postproc,
    badcase_lookup=_store.lookup,
    fallback=_fallback,
)