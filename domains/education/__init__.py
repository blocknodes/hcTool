"""教育（education）域。

导出唯一 Domain 实例。契约见 app.domain.Domain。
- tools：来自同目录 schema.json。
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
    key="education",
    name="教育",
    tools=tools,
    pipeline=_pipeline,
    postprocess=_postproc,
    badcase_lookup=_store.lookup,
    fallback=_fallback,
)
