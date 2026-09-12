"""qa 域 L2 badcase：零泛化精确覆盖。"""
from __future__ import annotations

import json
from pathlib import Path

from . import textkey


class BadcaseStore:
    def __init__(self, entries: list[dict]):
        self._map: dict[str, tuple[str, dict]] = {}
        for e in entries or []:
            raw = e.get("raw")
            if raw:
                self._map[textkey.normalize(raw)] = (e.get("tool", ""), e.get("params", {}))

    def lookup(self, raw: str):
        return self._map.get(textkey.normalize(raw))

    @classmethod
    def load(cls, path: Path):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(payload.get("entries", []))
        except Exception:  # noqa: BLE001
            return cls([])
