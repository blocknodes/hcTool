"""少儿（children）域。

导出唯一 Domain 实例。契约见 app.domain.Domain。
"""

from __future__ import annotations

from pathlib import Path

from app.domain import Domain, _load_schema_json
from app.examples import ExampleBank

from .rules import apply as _rule_apply
from .postproc import normalize as _postproc
from .badcase import BadcaseStore
from .fallback import fallback as _fallback

_dir = Path(__file__).resolve().parent

tools = _load_schema_json(_dir)

_bank = ExampleBank.from_testset(_dir / "testset.json")

_store = BadcaseStore.load(_dir / "badcases.json")

domain: Domain = Domain(
    key="children",
    name="少儿",
    tools=tools,
    example_bank=_bank,
    rule_select=_rule_apply,
    postprocess=_postproc,
    badcase_lookup=_store.lookup,
    fallback=_fallback,
)