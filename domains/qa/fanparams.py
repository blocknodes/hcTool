"""fan_knowledge_agent（泛知识问答）参数构造（hcTools 侧）。

契约对齐 juagent `domains/core/pipeline.py` 的 fan_knowledge_agent 分支：
必填 messages + riskRespStrategy；sceneType 默认 knowledge_qa，带内容域词给对应枚举；
need_media 决定问答是否附带媒资检索结果。
同名实现也在 hcAgent/app/fanparams.py（engine 侧），此处为 hcTools 域流水线自用，
逻辑保持一致。空参数 {} 是错的——产品 schema 要求 messages + riskRespStrategy 必填。
"""
from __future__ import annotations

import re

_FAN_SCENE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"少儿|动画|卡通|佩奇|汪汪队|幼儿|绘本|儿歌"), "child"),
    (re.compile(r"教育|学习|课程|知识(?:点|课)|课文|老师(?:讲|教)|数学|物理|化学|"
                 r"公式|古诗|单词"), "education_qa"),
    (re.compile(r"音乐|歌曲|歌星|歌手|专辑|曲子|MV|旋律|音符"), "agent_music"),
    (re.compile(r"体育|比赛|赛事|比分|球队|运动员|联赛|世界杯|NBA|中超|奥运"), "sport_qa"),
    (re.compile(r"影视|电影|电视剧|剧集|综艺|纪录片|影片|剧(?:里|中)?|演员|导演|演(?:的|过|着)?"),
     "video"),
    (re.compile(r"有声|听书|小说|电台|播客|评书"), "audio_qa"),
    (re.compile(r"设备|遥控|音量|亮度|画质|机顶盒|投屏|关机|开机"), "device_control_qa"),
]


def fan_scene_type(query: str) -> str:
    for pattern, scene in _FAN_SCENE_RULES:
        if pattern.search(query or ""):
            return scene
    return "knowledge_qa"


def fan_need_media(query: str) -> bool:
    q = query or ""
    scene = fan_scene_type(q)
    if scene in {"sport_qa", "education_qa", "device_control_qa"}:
        return False
    if scene in {"video", "child", "audio_qa", "agent_music"}:
        return True
    wants_content = re.search(
        r"找|推荐|播放|想看|要看|看(?:一|下|些)?|来(?:一|个|部|首)?|"
        r"有哪些|哪些|给我|推荐(?:一下)?|搜(?:下|一下)?",
        q,
    )
    content_word = re.search(
        r"电影|电视剧|综艺|动画|纪录片|影片|片子|歌|歌曲|音乐|MV|"
        r"剧|演员|导演|角色|明星|球星|歌手|片段|台词",
        q,
    )
    return bool(wants_content and content_word)


def fan_params(query: str) -> dict:
    return {
        "messages": [{"role": "user", "content": query or ""}],
        "riskRespStrategy": "all_replace",
        "sceneType": fan_scene_type(query or ""),
        "need_media": fan_need_media(query or ""),
    }