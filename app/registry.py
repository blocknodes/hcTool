"""工具 schema 注册表。

LLM-first 原则：知识内聚到工具定义本身（description / enum），不再散落到外部
prompt guide 和后处理规则。这里只负责加载 schema 并转成 OpenAI function 定义。

schema 目录格式：
  schema/
    index.json                 # {"domains": [{"domain_key": "device", "domain": "设备", "file": "device.json"}]}
    device.json                # {"domain": "设备", "domain_key": "device", "tools": [ {tool_name, description, parameters}, ... ]}
    ...
若无 index.json，则加载目录下所有 *.json（除 index.json）作为域文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

# 网关 grammar 编译器常不支持的 JSON Schema 关键字，注入前裁掉（只是校验性约束）。
UNSUPPORTED_KEYS = {
    "uniqueItems", "minItems", "maxItems", "minLength", "maxLength",
    "minProperties", "maxProperties", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "allOf", "if", "then", "else", "default", "examples", "format",
}
META_KEYS = {"$schema", "name", "title"}


@dataclass
class Tool:
    name: str
    domain: str          # 内部中文域名
    domain_key: str      # 英文域标识
    description: str
    parameters: dict[str, Any]

    def as_openai_function(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": " ".join(self.description.split())[:1024],
                "parameters": _prune(self.parameters, top=True),
            },
        }


def _prune(node: Any, top: bool = False) -> Any:
    """递归去掉网关不支持的关键字；properties/definitions 的下一层是字段名空间，不删。"""
    if isinstance(node, list):
        return [_prune(item) for item in node]
    if not isinstance(node, dict):
        return node
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in UNSUPPORTED_KEYS or (top and key in META_KEYS):
            continue
        if key in {"properties", "definitions", "$defs", "patternProperties"} and isinstance(value, dict):
            result[key] = {name: _prune(spec) for name, spec in value.items()}
        else:
            result[key] = _prune(value)
    result.setdefault("type", "object") if top else None
    return result


@dataclass
class Registry:
    tools: list[Tool] = field(default_factory=list)

    @property
    def by_name(self) -> dict[str, Tool]:
        return {t.name: t for t in self.tools}

    @property
    def by_domain_key(self) -> dict[str, list[Tool]]:
        out: dict[str, list[Tool]] = {}
        for t in self.tools:
            out.setdefault(t.domain_key, []).append(t)
        return out

    def candidates(self, domain_key: str | None) -> list[Tool]:
        """给定域标识则只返回该域工具；否则返回全部（LLM-first 自动路由）。"""
        if domain_key:
            return self.by_domain_key.get(domain_key, [])
        return list(self.tools)

    def openai_tools(self, domain_key: str | None = None) -> list[dict[str, Any]]:
        return [t.as_openai_function() for t in self.candidates(domain_key)]


def _load_domain_payload(payload: dict[str, Any]) -> list[Tool]:
    domain = payload.get("domain", "")
    domain_key = payload.get("domain_key", "")
    tools: list[Tool] = []
    for item in payload.get("tools", []):
        params = item.get("parameters")
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        # 有的 schema 把入参包在 parameters.parameters 里，剥一层
        if "properties" not in params and isinstance(params.get("parameters"), dict):
            params = params["parameters"]
        tools.append(Tool(
            name=item["tool_name"],
            domain=domain,
            domain_key=domain_key,
            description=item.get("description", "") or item.get("title", ""),
            parameters=params,
        ))
    return tools


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    schema_dir = SCHEMA_DIR
    if not schema_dir.exists():
        return Registry(tools=[])

    index_path = schema_dir / "index.json"
    files: list = []
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        files = [schema_dir / entry["file"] for entry in index.get("domains", [])]
    else:
        files = [p for p in schema_dir.glob("*.json") if p.name != "index.json"]

    tools: list[Tool] = []
    for path in files:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        tools.extend(_load_domain_payload(payload))
    return Registry(tools=tools)
