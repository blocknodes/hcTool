"""audio 域 DSL 占位。

audio 工具为扁平参数（action/query/category/time），无嵌套 DSL 结构。
保留 dsl 模块以对齐 vod 三层流水线契约。
"""
from __future__ import annotations


def route_tool(query: str) -> str:
    return "audio_search"


def build_search_dsl(query: str) -> dict | None:
    return None
