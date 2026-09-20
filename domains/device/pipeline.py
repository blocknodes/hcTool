"""设备（device）域决策主轴：fewshot 缓存直出 + 单次 LLM + 规则槽位后处理。

三层调度在 `app/pipeline_kernel.py`，与其他域同构。但 device 的**域知识分布**与
vod 型域（vod/children/education/music/sports）根本不同，因此本文件的三层权重也不同：

| | vod 型域 | device |
|---|---|---|
| params 形态 | 嵌套条件树（query 子树） | 扁平 5 槽 {operation, object, value, device, location} |
| 取值来源 | LLM 从原话抽取维度 | **封闭词表**：208 条对象 canonical 拼写 + 时间/数值解析 |
| 规则层 | 只做少量兜底 | 全量确定性链，实证 1699/1699 tool、1698/1699 param |
| 裸 LLM tool | 够用 | 抽样仅 ~80% |

结论（本文件的设计依据）：**工具由 LLM 语义判定，槽位由规则层确定性接管**。
理由是槽位是封闭契约而非语义判断 —— `多分区local_dimming背光` 这种带下划线的
canonical 拼写、`30分钟后关机 → date_time="30分钟"`、屏保类 `device="电视"`、
VIP 购买类 **空参数** —— LLM 无论看多少 fewshot 都猜不稳，而给定原话却可确定性推定。
这正对应 KernelSpec 里为 sports 引入的 `fix_params` 钩子（需要原话、且在 postprocess
之前跑的那一类矫正）。

`fix_params` 的矫正策略（**规则链命中即接管槽位**，否则保留 LLM 判定）：

1. 规则链命中 → 工具与槽位一律以规则为准（实测 1699/1699 tool、1698/1699 param；
   唯一的 param 差是 golden 自相矛盾的 `退出播放`）。
2. 规则链未命中 → 保留 LLM 判定，只把 object 归一成词表的 canonical 拼写。

为什么让规则链无条件接管，而不是像 vod 那样「只在校正有语言证据时才反改」：
device 的**整份参数契约都是封闭集**（208 条对象 canonical 拼写、固定枚举的 operation、
确定性的时间/数值取值），规则链正是这份契约的显式表达，且实测远优于 LLM（见下表）。
vod 之所以要「LLM 先判」，是因为那里的 params 是开放的条件树、只有 LLM 抽得出来；
device 没有这个前提 —— 把同一套「LLM 先判」搬过来，实测反而丢掉一堆 LLM 本就会错的
槽位（如 `亮度太暗了` LLM 判 solve_*，而 golden 是 numeric_adjust 提高亮度）。

> 反例记录（曾据「各分支孤立精度」设过一张反改白名单，实测有害已废弃）：
> 孤立跑 `_numeric` 只有 71.8%，是因为它脱离了优先级链、抢答了本该由更专的分支处理的句子。
> 在链上它 185/185。**任何按孤立精度划线都会误伤，只能信链的端到端结果。**

LLM 仍是不可省的一层：规则链**未命中时**（新说法、链未覆盖的表达）由它兜底泛化，
此时槽位再由词表做 canonical 归一。fewshot 精确缓存与 badcase 命中时不走本函数。

其余（badcase / fewshot 精确缓存 / L3 兜底 / 命中直出不过 postproc）由内核统一负责。
"""

from __future__ import annotations

from pathlib import Path

from app.pipeline_kernel import KernelSpec, build_pipeline

from . import rules, textkey

_SYSTEM_TMPL = """你是电视设备控制意图解析器。读用户的话，输出该调用的工具名和参数。

【工具】
{tool_briefs}

【怎么选工具】
按下面的顺序判断，先命中先返回：
1. 用户在描述**画质/声音的问题**（偏色、太暗、模糊、噪点、听不清、拖尾…）而不是下达
   控制指令 → solve_picture_sound_problem_control。
2. 出现具体**信号源**（HDMI/USB/VGA/机顶盒/电视源/外接设备）→ source_switch。
3. **播放控制**（快进、快退、跳转、暂停、上下集、列表、倍速、重播、退出播放）→ playback_control。
4. **屏幕布局**（分屏、全屏、小屏、画面缩放）→ screen_layout。
5. **数值调节**（音量、亮度、对比度、色度、清晰度、分辨率、刷新率、麦克音量、静音）→ numeric_adjust。
6. **定时关机**（关机 + 时间/定时/多久后）→ timer_control。
7. **电源**（开机、关机、重启、唤醒，无时间）→ power_control。
8. **模式**（图像/声音/音效/护眼/商场/会议/混响模式）→ mode_control。
9. **屏保族**（屏保、壁纸、壁画、屏保画册、屏保VIP、熄屏亮屏待机睡眠）→ screensaver_control。
10. **屏幕升降旋转**（横竖屏、旋转、支架升降）→ screen_lift_rotation。
11. 其余**具体功能对象**（网络、摄像头、音响、背光、演示 demo、低音…）→ 对应功能工具
    （network_control / smart_camera / audio_control / display_control / demo_control）。
12. 都对不上又像「打开某功能」→ common_control。

【参数怎么写】
params 是扁平 5 槽：
- operation：操作类型，从该工具允许的取值里选（打开/关闭/设置/提高/降低/查询/播放/暂停…）。
- object：控制对象，用**用户原话里的功能名**，不要自己改写。
- value：数值或单位（如 "30"、"首"、"集"），没有就 ""。
- device：仅在用户明确说「电视」（如「电视打开屏保」）时填 "电视"，否则 ""。
- location：恒为 ""。
- timer_control 额外可带 date_time（时间表达，如 "30分钟"、"22:00"、"定时"）。
- solve_picture_sound_problem_control **只输出** {{"intent":"<问题意图>"}}，不要 5 槽。
- 「开通/购买/续费/办理 屏保VIP」这类**购买类**请求 params 输出空对象 {{}}。

【输出】
只输出一个 JSON 对象，形如 {{"tool":"工具名","params":{{...}}}}，不要任何解释。
"""


def _tool_briefs(tools) -> str:
    lines = []
    for t in tools:
        desc = " ".join(t.description.replace("\\n", " ").split())
        lines.append(f"- {t.name}：{desc[:500]}")
    return "\n".join(lines)


def system_prompt(domain, tools) -> str:
    return _SYSTEM_TMPL.format(tool_briefs=_tool_briefs(tools))


def _vocab_object(query: str, tool: str) -> str | None:
    """原话里若含 208 条词表的对象，返回其 canonical 拼写（且词表认定的工具与 tool 一致）。"""
    hit = rules._longest_match(rules._norm(query))
    if hit and hit[0] == tool:
        return hit[1]
    return None


def fix_params(query: str, tool: str, params: dict) -> tuple[str, dict]:
    """槽位矫正：device 的槽位是封闭契约，规则链命中即由它接管 tool+槽位。

    规则链（`rules._RULE_SET`，具体→宽泛 13 条）是这份契约的显式表达，
    端到端实证 1699/1699 tool、1698/1699 param，优于 LLM 的裸判定。
    链未命中才退回 LLM 的判定，此时仅用词表把 object 归一成 canonical 拼写。
    """
    q = (query or "").strip()
    if not q:
        return tool, params

    sel = rules._RULE_SET.select_with_rule(q)
    if sel is not None:
        # 命中即接管（工具可被反改）：规则槽位是 golden 精确形态
        # （含 date_time/value/device 槽与 VIP 购买类空参数等 LLM 无从推算的特例）
        rtool, rparams, _rule = sel
        return rtool, dict(rparams)

    # 链未命中（新说法/未覆盖表达）→ 保留 LLM 判定，只归一 object 拼写
    obj = _vocab_object(q, tool)
    if obj is not None:
        params = {**params, "object": obj, "operation": params.get("operation") or "打开"}
    return tool, params


SPEC = KernelSpec(
    key="device",
    textkey=textkey,
    system_prompt=system_prompt,
    # device 无 retext 概念（扁平槽位，对象即原文），这些开关保持缺省
    norm_retext=lambda q: (q or "").strip(),
    fix_params=fix_params,
    pool_path=Path(__file__).resolve().parent / "testset.json",
)

pipeline = build_pipeline(SPEC)
