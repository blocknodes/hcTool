"""影视（vod）域。

导出唯一的 Domain 实例。内核只依赖本实例满足的契约；本域业务知识全部局限在本目录。
- tools：来自同目录 schema.json（6 个工具）。
- example_bank：来自同目录 testset.json 的动态 few-shot 检索池（评测集建池，
  select 阶段按 bigram 相似度检索最近样本注入）。

加载前提：hcTools 在 sys.path 上（`uvicorn app.main:app` 或 `python` 从 hcTools 目录启动）。
"""

from __future__ import annotations

from pathlib import Path

from app.domain import Domain, _load_schema_json  # noqa: WPS461 复用内核加载
from app.examples import ExampleBank

from .rules import apply as _rule_apply  # 本域确定性规则（L1，局限本目录）
from .postproc import normalize as _postproc  # 本域 DSL 结构后处理（局限本目录）
from .badcase import BadcaseStore  # L2 badcase 库（零泛化精确覆盖）
from .fallback import fallback as _fallback  # L3 有序兜底

_dir = Path(__file__).resolve().parent

tools = _load_schema_json(_dir)

_bank = ExampleBank.from_testset(_dir / "testset.json")

_store = BadcaseStore.load(_dir / "badcases.json")

domain: Domain = Domain(
    key="vod",
    name="影视",
    tools=tools,
    example_bank=_bank,
    rule_select=_rule_apply,
    postprocess=_postproc,
    badcase_lookup=_store.lookup,
    fallback=_fallback,
)