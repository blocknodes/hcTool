"""education 域 DSL 占位：结构由 rules.build_dsl 生成，本模块对齐流水线契约。"""
from __future__ import annotations


def route_tool(query: str) -> str:
    return "edu_search"


def build_search_dsl(query: str) -> dict | None:
    return None
