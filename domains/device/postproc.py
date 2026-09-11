"""device 域参数后处理（规范化）。

设备工具参数为扁平 5 槽，rules 已确定性生成；此处仅保证返回干净 dict。
"""
from __future__ import annotations


def normalize(tool: str, params: dict) -> dict:
    if not isinstance(params, dict):
        return params or {}
    return params


postprocess = normalize