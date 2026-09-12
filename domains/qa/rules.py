"""qa（泛知识）域 L1 规则层。

开放域知识问答/推理/元信息查询都收口到 fan_knowledge_agent（空参数，query 走消息）。
判定只在高置信问答信号时触发，避免误伤 播放/片段/设备/教育课程。
"""
from __future__ import annotations

import re

from .fanparams import fan_params

_QA = re.compile(
    r"谁|什么|吗|呢|多少|哪些|哪几|哪部|哪一|哪位|哪年|几时候|什么时候"
    r"|为什么|为何|怎么|怎样|好不好|是不是|有没有|到底是|是什么"
    r"|简介|扮演|主演|演员表|短评|票房|获奖|奖项|作曲|写词|作词|专辑"
    r"|出自|成立|上映|讲了|讲述|多大了"
    r"|唱的歌|听得|最好听|比较好|治愈系"
)


def apply(query: str) -> tuple[str, dict] | None:
    q = (query or "").strip()
    if not q:
        return None
    if _QA.search(q):
        return ("fan_knowledge_agent", fan_params(q))
    return None
