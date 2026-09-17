"""兼容垫片：fan_knowledge_agent 参数构造已上移到内核 `app/fanparams.py`
（music 域也出这个工具，实现留在 qa 域会让 music 反向依赖 qa，故共置内核）。

这里只做转发，逻辑与常量统一维护在 `app.fanparams`。
"""
from __future__ import annotations

from app.fanparams import (  # noqa: F401
    fan_intent,
    fan_params,
    fan_scene_intents,
    fan_scene_type,
)

__all__ = ["fan_scene_type", "fan_intent", "fan_scene_intents", "fan_params"]
