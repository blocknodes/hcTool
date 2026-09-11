"""轻量 schema 校验：只覆盖工具契约需要的 JSON Schema 子集。

只做校验并把违规项回传给模型自修复，不替模型改写答案（区别于旧方案的 postproc）。
"""

from __future__ import annotations

from typing import Any


def check(params: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """返回违规描述列表；空列表表示通过。"""
    issues: list[str] = []
    if not isinstance(schema, dict):
        return issues
    props = schema.get("properties")
    if not isinstance(props, dict):
        return issues

    required = schema.get("required") or []
    additional = schema.get("additionalProperties", True)

    for name in required:
        if name not in params:
            issues.append(f"缺少必填字段 {name}")

    for name, value in params.items():
        spec = props.get(name)
        if spec is None:
            if additional is False:
                issues.append(f"多余字段 {name}")
            continue
        issues.extend(_check_value(name, value, spec))
    return issues


def _check_value(name: str, value: Any, spec: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not isinstance(spec, dict):
        return issues

    expected = spec.get("type")
    if expected and not _type_ok(value, expected):
        issues.append(f"字段 {name} 类型应为 {expected}")
        return issues

    enum = spec.get("enum")
    if isinstance(enum, list) and value not in enum:
        issues.append(f"字段 {name} 取值 {value!r} 不在允许范围")

    if expected == "array" and isinstance(value, list):
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for i, item in enumerate(value):
                issues.extend(_check_value(f"{name}[{i}]", item, item_spec))

    if expected == "object" and isinstance(value, dict):
        issues.extend(check(value, spec))

    return issues


def _type_ok(value: Any, expected: str | list) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(value, t) for t in expected)
    mapping = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    py = mapping.get(expected)
    if py is None:
        return True
    if expected == "integer" and isinstance(value, bool):
        return False
    if expected == "number" and isinstance(value, bool):
        return False
    return isinstance(value, py)
