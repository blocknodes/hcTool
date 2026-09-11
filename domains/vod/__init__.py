"""影视（vod）域。

导出唯一的 Domain 实例。内核只依赖本实例满足的契约；本域业务知识全部局限在本目录。
第一版：tools 来自同目录 schema.json，prompt / few-shot / 前后处理均用内核默认。

加载前提：hcTools 在 sys.path 上（`uvicorn app.main:app` 或 `python` 从 hcTools 目录启动）。
"""

from __future__ import annotations

from pathlib import Path

from app.domain import Domain, _load_schema_json  # noqa: WPS461 复用内核加载

_dir = Path(__file__).resolve().parent

tools = _load_schema_json(_dir)
domain: Domain = Domain(
    key="vod",
    name="影视",
    tools=tools,
)