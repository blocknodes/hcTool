"""qa 域文本归一：只需去空白/半角/标点，用于 badcase key。"""
from __future__ import annotations

import re


def normalize(text: str) -> str:
    return re.sub(r"[^\w一-龥A-Za-z0-9]", "", (text or "").strip().lower())
