"""music 域 L3 兜底。"""
from __future__ import annotations
from .rules import apply

def fallback(query: str) -> tuple[str, dict]:
    q = (query or "").strip()
    return "music_song_search", {"retext": q}
