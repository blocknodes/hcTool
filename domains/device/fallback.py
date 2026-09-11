"""device 域 L3 兜底：保证任何 query 都有非空返回。

设备域常见特性对象由 L1 词典覆盖；未命中时落最通用的 common_control。
兜底策略：剥离打开动词后其余串作为 object（小写归一）。
特例：音乐功能 → 音乐播放器。
"""
from __future__ import annotations

import re


def fallback(query: str) -> tuple[str, dict]:
    t = query.strip()
    for v in ("打开", "启动", "进入", "开启", "切换", "调用", "启用", "展开", "进行"):
        if t.startswith(v):
            t = t[len(v):]
            break
    t = re.sub(r"的$", "", t).strip().lower().replace(" ", "")
    if t == "音乐功能":
        t = "音乐播放器"
    return "common_control", {"operation": "打开", "object": t, "value": "",
                              "device": "", "location": ""}