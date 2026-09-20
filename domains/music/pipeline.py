"""音乐（music）域决策主轴：fewshot 缓存直出 + 单次 LLM 出 tool&params。

三层调度在 `app/pipeline_kernel.py`（与 vod/children/education 同构）。域特有部分：

- **9 个工具，全部扁平槽位**（无嵌套 query DSL），故 `all_fields` 为空、
  `canon_query` 不做任何改写。工具之间没有包含关系 → 无 umbrella、无下钻，
  由 LLM 直接选。
- 工具族（按 golden 实证的触发信号）：
    music_ksong_search        K歌/唱歌
    music_song_mv_search      MV/音乐视频
    music_song_qqmusic_search QQ音乐（keywords）
    tvchannel_music_search    电视频道/CCTV/卫视
    music_song_history        历史/听过/播放记录
    music_song_favorite_search 收藏
    music_song_recommend      随便听/来点歌
    music_song_semantic_search 歌曲语义检索（说不清歌名时的描述）
    music_song_search         其余默认（歌名/歌手/专辑/作词/作曲/标签/歌词/榜单）
- `retext` 约定：**保留原话**（实测 identity 408/485 = 84.1%，其余是 golden 自身的
  逐条改写噪声，如「换个一路向北」→「播放一路向北」），故只做 strip。
"""

from __future__ import annotations

from pathlib import Path

from app.pipeline_kernel import KernelSpec, build_pipeline

from . import textkey

# 全部 9 个工具都吃 retext
_ALL_TOOLS = {
    "music_song_search", "music_song_semantic_search", "music_song_mv_search",
    "music_ksong_search", "music_song_qqmusic_search", "music_song_recommend",
    "music_song_history", "tvchannel_music_search", "music_song_favorite_search",
}

_FIELD_CATALOG = """- song 歌名
- singer 歌手
- album 专辑名
- version 版本（如 原唱/翻唱/伴奏/纯音乐）
- gender 歌手性别
- ip 影视/动漫 IP 名（如 主题曲所属作品）
- tag 标签（如 摇滚/民谣/经典老歌/儿歌）
- lyrics 歌词片段（用户说了某句歌词时填这里）
- toplist 榜单名（如 热歌榜/新歌榜）
- lyricist 作词人
- composer 作曲人
- keywords QQ音乐关键词（仅 music_song_qqmusic_search 用）"""

_SYSTEM_TMPL = """你是音乐语音助手的意图解析器。读用户的话，输出该调用的工具名和参数。

【工具】
{tool_briefs}

【怎么选工具】
按下面的顺序判断，**先命中先返回**：
1. 说了 **K歌/唱歌/想唱** → music_ksong_search。
2. 说了 **MV/音乐视频/这首歌的视频** → music_song_mv_search。
3. 说了 **QQ音乐** → music_song_qqmusic_search（填 keywords）。
4. 说了 **电视频道/卫视/CCTV/某台** → tvchannel_music_search。
5. 要**回看听歌记录**（听过/历史/播放记录）→ music_song_history。
6. 要**看收藏**（收藏/我喜欢）→ music_song_favorite_search。
7. **说清了歌名/歌手**（或专辑/作词/作曲/榜单）→ music_song_search。
8. **说不清是什么歌、只能用描述找**（「那个什么什么歌」「节奏很欢快的」「适合婚礼放的歌」）
   → music_song_semantic_search。
9. 单纯要**推歌**没给具体歌名（随便听/来点歌/换一批）→ music_song_recommend。

【参数怎么写】
所有工具的 params 都必须有 `retext` = **用户原话**（原样填，不要自己改词、不要翻译）。
再按用户话里提到的维度补槽位：
- 单值槽位：`"song": ["歌名"]`、`"singer": ["歌手"]` —— **值一律用数组**。
- `lyrics` 用于用户说了某句歌词；`toplist` 用于榜单名；`keywords` 仅 QQ音乐用。
- 用户**没提到**的槽位不要出现（不要填 null / 空串 / 空数组）。

可用槽位：
{fields}

【输出】
只输出一个 JSON 对象，形如 {{"tool":"工具名","params":{{...}}}}，不要任何解释。
"""


def norm_retext(q: str) -> str:
    """音乐域 retext 保留原话（golden 只有 strip 级差异，其余是逐条改写噪声）。"""
    return (q or "").strip()


def _tool_briefs(tools) -> str:
    lines = []
    for t in tools:
        desc = " ".join(t.description.replace("\\n", " ").split())
        lines.append(f"- {t.name}：{desc[:900]}")
    return "\n".join(lines)


def system_prompt(domain, tools) -> str:
    return _SYSTEM_TMPL.format(tool_briefs=_tool_briefs(tools), fields=_FIELD_CATALOG)


SPEC = KernelSpec(
    key="music",
    textkey=textkey,
    system_prompt=system_prompt,
    norm_retext=norm_retext,
    retext_tools=_ALL_TOOLS,
    search_family=_ALL_TOOLS,
    # 扁平槽位、工具间无包含关系：全暴露、不下钻
    umbrella="",
    drill=None,
    all_fields=set(),   # 无嵌套 query，canon_query 不介入
    fuzzy_tool="",
    oor_words=(),
    pool_path=Path(__file__).resolve().parent / "testset.json",
)

pipeline = build_pipeline(SPEC)
default_pool = pipeline.default_pool
