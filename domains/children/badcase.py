"""L2 badcase 库：零泛化的精确覆盖。

数据在 badcases.json；这里负责加载 + 归一 + 精确匹配。
badcase 是本义"精确止血"--一条坏样例对一个精确归一 key，命中即直出 tool+params，
覆盖一切（优先级最高）。不做任何泛化；泛化的活交给 L1 rules / dsl / postproc。

加载失败不可拖垮域：try/except → 空库 + 告警（单域失败隔离）。
冲突 key 必须告警（两条 badcase 归一后同形 = 数据问题）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import textkey

logger = logging.getLogger("hcTools.domains.vod.badcase")


class BadcaseStore:
    def __init__(self, entries: list[dict]):
        self._map: dict[str, tuple[str, dict]] = {}
        for e in entries or []:
            raw = e.get("raw")
            if not raw:
                continue
            k = textkey.normalize(raw)
            if k in self._map:
                logger.warning("badcase 冲突 key=%s，后者覆盖（%r / %r）", k, self._map[k], raw)
            self._map[k] = (e.get("tool", ""), e.get("params", {}))

    @classmethod
    def load(cls, path: Path) -> "BadcaseStore":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(payload.get("entries", []))
        except Exception as exc:  # noqa: BLE001 加载失败不可拖垮域
            logger.error("badcases 加载失败（%s），使用空库：%s", path, exc)
            return cls([])

    def lookup(self, query: str) -> tuple[str, dict] | None:
        """精确命中返回 (tool, params)，否则 None。"""
        return self._map.get(textkey.normalize(query))

    def __len__(self) -> int:
        return len(self._map)