"""qa（泛知识）域 L1 规则层 —— 规则表驱动。

开放域知识问答/推理/元信息查询都收口到 fan_knowledge_agent（空参数，query 走消息）。
判定只在高置信问答信号时触发，避免误伤 播放/片段/设备/教育课程。

迁移自原 apply()：单条问答判定变为 RuleSet 的 decide 规则，行为零改动。
"""
from __future__ import annotations

import re

from app.rulebase import Rule, RuleSet

from .fanparams import fan_params

_QA = re.compile(
    r"谁|什么|吗|呢|多少|哪些|哪几|哪部|哪一|哪位|哪年|几时候|什么时候"
    r"|为什么|为何|怎么|怎样|好不好|是不是|有没有|到底是|是什么"
    r"|简介|扮演|主演|演员表|短评|票房|获奖|奖项|作曲|写词|作词|专辑"
    r"|出自|成立|上映|讲了|阐述|多大了"
    r"|唱的歌|听得|最好听|比较好|治愈系"
)


def _qa_decide(q: str):
    """原 apply 单分支：命中问答信号 → fan_knowledge_agent(空参数)；否则 None。"""
    if _QA.search(q):
        return ("fan_knowledge_agent", fan_params(q))
    return None


RULE_SET = RuleSet(
    rules=[
        Rule(
            id="qa_open_knowledge",
            tool="fan_knowledge_agent",
            priority=1,
            title="泛知识问答",
            explain="open域知识/推理/元信息查询 → fan 问答(空参数,query走消息)",
            decide=_qa_decide,
        ),
    ],
)


def apply(query: str):
    """入口：RuleSet.select。返回 (tool, params) 或 None。"""
    if not query or not query.strip():
        return None
    return RULE_SET.select(query)


__all__ = ["apply"]