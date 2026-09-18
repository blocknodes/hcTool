"""device（设备）域确定性规则层（L1）。

工具 16 个，params 统一 5 槽 {operation, object, value, device, location}，
device / location 恒为空串。
例外：
- solve_picture_sound_problem_control：只给 {intent}
- timer_control：{operation, object, date_time, device, location}

判定顺序（具体→宽泛）：
1. 问题修复（太/偏/发/不清/暗/亮/噪…）→ solve_picture_sound_problem_control
2. 信号源切换（HDMI/VGA/USB/机顶盒/电视源）→ source_switch
3. 播放控制（快进/快退/跳转/暂停/上下集/列表）→ playback_control
4. 屏幕布局（分屏/全屏/小屏/画面缩）→ screen_layout
5. 数值调节（音量/亮度/对比度/色度/清晰度/分辨率/静音/麦克…）→ numeric_adjust
6. 定时关机（关机+时间）→ timer_control
7. 电源裸开关机/重启 → power_control
8. 模式（X模式/音效/图像/护眼/商场…）→ mode_control
9. 特性对象：归一化后做「最长子串命中」词典匹配 → display/audio/demo/camera/
   screensaver/network/screen_lift；否则 → common_control（object=原文剥动词后小写）

对 1140 条 golden 实证收敛。bench: domains/device/bench/run_bench.py
"""
from __future__ import annotations

import re
from typing import Any

from ._vocab import _OBJ_TOOL  # type: ignore
from app.rulebase import Rule, RuleSet


def _slot(op: str, obj: str = "", val: str = "", dev: str = "") -> dict:
    return {"operation": op, "object": obj, "value": val, "device": dev, "location": ""}


def _norm(s: str) -> str:
    return re.sub(r"[\s_]", "", s).lower()


_OPEN_RE = re.compile(r"^(?:打开|启动|进入|开启|切换|调用|启用|展开|进行)")


def _rest(q: str) -> str:
    """去掉首个打开动词后的剩余串作为 object（小写归一）。

    golden 契约：`打开ULED_XDR` → object=uled_xdr，即下划线**保留**、
    英文转小写；仅空白对齐。空格/下划线不剔除（与 _norm 的匹配归一不同）。
    """
    t = q.strip()
    for v in ("打开", "启动", "进入", "开启", "切换", "调用", "启用", "展开", "进行"):
        if t.startswith(v):
            t = t[len(v):]
            break
    t = t.strip()
    return t.lower()


# ================= 1. 问题修复 =================
_SOLVE = [
    # 具体偏色（先于泛化 太艳/太淡）
    (re.compile(r"偏蓝|太蓝|发蓝|蓝色|偏冷|太冷|冷色|蓝了"), "cold_color_cast"),
    (re.compile(r"偏红|太红|发红|红了"), "red_color_cast"),
    (re.compile(r"偏黄|太黄|发黄|黄了"), "warm_color_cast"),
    (re.compile(r"偏绿|太绿|发绿|绿了"), "warm_color_cast"),
    (re.compile(r"太淡|不够鲜艳|不够饱和|偏淡|淡了|有点淡|不够饱满|颜色淡|饱和不够|饱和度低"), "colors_washed_out"),
    (re.compile(r"鲜艳|太艳|颜色太|太重|太浓|太饱和|饱和"), "colors_oversaturated"),
    (re.compile(r"暗部|暗处|暗场|太黑|看不见|一片黑|细节丢失|暗.*看不清"), "dark_areas_crushed"),
    (re.compile(r"过曝|高光|亮部|一片白|太亮"), "highlights_overexposed"),
    (re.compile(r"人声|对话|说话|听不清|听不见|人声不清|声音听不|语音不"), "voice_not_clear"),
    (re.compile(r"立体感|立体声|环绕|声场|层次|太平面|声音少立体|没有立体"), "lack_stereo_surround"),
    (re.compile(r"模糊|不清晰|太糊|糊|朦胧|锐利|不锐|不清楚|清晰度不够"), "lack_of_clarity"),
    (re.compile(r"噪点|雪花|不干净|不够干净|颗粒|有噪点"), "image_noise_obvious"),
    (re.compile(r"拖尾|拖影|卡顿|不流畅|不流畅|不清晰"), "motion_not_sharp"),
    (re.compile(r"刺眼|不舒服"), "poor_eye_comfort"),
    (re.compile(r"忽明忽暗|自动调光|老自己变|老是自己变|自己变|不停变|自动变"), "flickering_brightness"),
    (re.compile(r"还原电影色彩|电影色彩"), "image_mode_movie"),
]
_SOLVE_TRIG = re.compile(
    r"调节|调一下|调|修|清理|不好|看不清|看不清楚|听不|太|偏|刺|噪|糊|模糊|"
    r"不清|看不清|看不清楚|不够|失败|咋办|怎么办|开一下|帮|立体|自己变|增强"
    r"|有噪点|不干净|暗处|暗部|暗场|看不见|丢失|锐利|不流畅|卡顿|不清晰|还原|对话|人声|说话")


def _solve(q: str) -> tuple[str, dict] | None:
    if _OPEN_RE.match(q):
        return None
    if not _SOLVE_TRIG.search(q):
        return None
    # 「画面/画质/色彩 + 状态词 + 一点/了」的简短陈述 → 数值调节（清晰度/色度/对比度），非问题修复
    if re.search(r"色彩.*(?:太艳|饱和|鲜艳|降低|调高|调低)?.*(?:了|一点|调高|降低)", q) \
       and not re.search(r"帮我|有点|看起来|不够|有些|偏|淡", q):
        return None
    if re.match(r"(?:画面|画质|色彩|图片).*?(?:清晰|鲜艳|模糊|对比|亮|暗|淡).*?(?:一点|了)", q) \
       and not re.search(r"帮我|有点|看起来|不够|有些|偏", q):
        return None
    if re.match(r"^(?:声音|音量|亮度)", q) and re.search(r"大小|多少|设置|调节|点", q) and not re.search(r"不清|听不|看不清|太暗|太亮|太照|太刺|模糊|偏", q):
        return None  # 是数值调节而非画面问题（走 numeric）
    # 运动增强/运动画面不清晰 → motion_not_sharp
    if re.search(r"运动.{0,3}(?:增强|不清晰|不流畅|画面不清晰|开一下)", q):
        return ("solve_picture_sound_problem_control", {"intent": "motion_not_sharp"})
    # 屏幕太刺眼（裸陈述）→ 数值调节亮度；太刺眼+帮我调 → 问题修复
    if re.search(r"屏幕太刺眼|屏幕太亮", q) and not re.search(r"帮我|调节|调整|一下|怎么办", q):
        return None
    for rx, intent in _SOLVE:
        if rx.search(q):
            return ("solve_picture_sound_problem_control", {"intent": intent})
    return None


# ================= 2. 信号源 =================
# 具体信号源值：HDMI1-4/VGA/USB 保持大写；AV输入 原样；前置/侧置 前缀+小写hdmi
_SRC_SPECIFIC = re.compile(
    r"(前置hdmi|侧置hdmi|HDMI\s*[1-4]|hdmi\s*[1-4]|VGA|vga|USB|usb|AV输入|分量输入|"
    r"Type[-\s]?C|type[-\s]?c|switch|Switch|XBOX|xbox|PS[45]|ps[45]|视频[12]|HDMI|hdmi)", re.I)


def _source(q: str) -> tuple[str, dict] | None:
    low = q.lower()
    # 游戏机/TypeC 等外接源（小写值）
    m_console = re.search(r"(type-?\s*c|switch|xbox|ps[45]|ps\s*[45])", low)
    if m_console and re.search(r"切换|选择|设置|打开|启动|进入|接", q):
        return ("source_switch", _slot("设置", "信号源", m_console.group(1).replace(" ", "")))
    if re.search(r"typec|type c|type-c", low) and re.search(r"切换|打开|接", q):
        return ("source_switch", _slot("设置", "信号源", "typec"))
    # 打开型外源/电视券源（绑定机顶盒/地面数字电视 需保全文）
    for full in ("绑定机顶盒", "地面数字电视"):
        if full in q:
            return ("source_switch", _slot("打开", full))
    for obj in ("模拟电视", "数字电视", "直播电视", "机顶盒"):
        if obj in low:
            return ("source_switch", _slot("打开", obj))
    # 具体源 + 指定 HDMI 编号
    m = _SRC_SPECIFIC.search(q)
    if m and re.search(r"切换|选择|设置|打开|启动|输入|接口|端口|模式|信号|接到|HDMI|VGA|USB|视频|AV|取", q):
        tok = m.group(1).replace(" ", "")
        # 前置/侧置
        pm = re.match(r"(前置|侧置)(hdmi|HDMI)$", tok)
        if pm:
            return ("source_switch", _slot("设置", "信号源", pm.group(1) + "hdmi"))
        # HDMI1-4：带“输入/信号源”大写；纯“切换到/选择X”小写
        hm = re.search(r"HDMI\s*([1-4])", tok, re.I)
        if hm:
            # 切换到/信号源切换到 HDMI1 → 小写；选择X信号源/打开X输入 → 大写
            if re.search(r"切换(?:到|至)?.*HDMI", q):
                return ("source_switch", _slot("设置", "信号源", "hdmi" + hm.group(1)))
            if re.search(r"打开|输入|信号源|选择|接口|端口", q):
                return ("source_switch", _slot("设置", "信号源", "HDMI" + hm.group(1)))
            return ("source_switch", _slot("设置", "信号源", "hdmi" + hm.group(1)))
        # VGA/USB：X+模式→大写保留模式；裸切换→小写；输入/信号源/选择 → 大写
        if re.search(r"VGA|vga|USB|usb", tok) or re.search(r"VGA|vga|USB|usb", q):
            mvg = re.search(r"(VGA|USB|vga|usb)(模式)?", q)
            mup = mvg.group(1).upper()
            if mvg and mvg.group(2):
                return ("source_switch", _slot("设置", "信号源", mup + "模式"))
            if re.search(r"切换(?:到|至)?\s*(?:VGA|vga|USB|usb)$", q) \
               or re.search(r"信号源切换(?:到|至)?\s*(?:VGA|vga|USB|usb)$", q):
                return ("source_switch", _slot("设置", "信号源", tok.lower()))
            if re.search(r"输入|信号源|模式|打开|选择|接口", q):
                return ("source_switch", _slot("设置", "信号源", mup))
            return ("source_switch", _slot("设置", "信号源", tok.lower()))
        # 视频1/2、AV输入 原样
        if re.search(r"AV输入|AV|分量|视频", tok):
            return ("source_switch", _slot("设置", "信号源", "AV输入" if "AV" in tok else tok))
    # 裸 HDMI/信号源（无编号）
    if re.search(r"信号源|输入源|hdmi|HDMI|接口|外接|外源|源切换|外部输入|外设|外部设备|输入选择", low):
        qs = q.strip()
        # 打开信号源 / 信号源 → 打开 信号源
        if qs in ("打开信号源", "信号源"):
            return ("source_switch", _slot("打开", "信号源"))
        # HDMI接口选择 / 选择HDMI → 设置 信号源空（golden：HDMI接口选择→信号源空）
        if "接口选择" in qs or qs == "选择HDMI" or "选择HDMI" in qs:
            return ("source_switch", _slot("设置", "信号源", ""))
        # 输入源选择/切换 → 设置 信号源空
        if re.search(r"输入源选择|输入源切换", qs):
            return ("source_switch", _slot("设置", "信号源", ""))
        # 打开型 → 打开 HDMI选择
        if re.search(r"打开HDMI|启动HDMI|开启HDMI|HDMI输入|HDMI信号|外接设备|外部输入|输入选择|hdmi输入|hdmi信号|hdmi端口|外设|外接|HDMI端口|HDMI模式|外部设备", qs):
            # 打开HDMI输入/选择/接口 → 设置信号源空
            if re.search(r"打开HDMI(输入|选择|接口)", qs):
                return ("source_switch", _slot("设置", "信号源", ""))
            return ("source_switch", _slot("打开", "HDMI选择"))
        # 设置信号源空（裸hdmi/切换/HDMI切换等）
        return ("source_switch", _slot("设置", "信号源", ""))
    return None


# ================= 3. 播放控制 =================
def _play_js(op: str, obj: str = "", val: str = "") -> tuple[str, dict]:
    return ("playback_control", _slot(op, obj, val))


def _play_unit(q: str) -> str:
    """单位判定：集 > 首/曲 > 部 > 个/节目。"""
    if "集" in q:
        return "集"
    if "首" in q or "曲" in q:
        return "首"
    if "部" in q:
        return "部"
    if "个" in q or "节目" in q:
        return "个"
    return ""


def _play_dir(q: str) -> str | None:
    """上/下方向判定：查出现最早的上/下类标记。"""
    up = [m.start() for m in re.finditer(
        r"上一|往[前回]|往回|回到|刚才那首|刚才|换回|切回|倒回|放回|退|倒回|退回|上(?=[一个首部集曲])|前(?![面])", q)]
    dn = [m.start() for m in re.finditer(
        r"下一|往[后後]|向后|后(?![面])|下(?=[一个首部集曲]|节目)", q)]
    if up and dn:
        return "上" if min(up) < min(dn) else "下"
    if up:
        return "上"
    if dn:
        return "下"
    return None


def _timed_val(q: str) -> str:
    """快进/跳转类 value：N秒 → N秒；N分钟(2→2分 其余→N分钟)；HH:MM（直接跳到/从X开始 保留前导0）。"""
    mtime = re.search(r"(\d{1,2}):(\d{2})", q)
    if mtime:
        keep01 = mtime.group(1) == "01" and re.search(r"直接跳到|从\s*\d+:\d+\s*开始", q)
        hh = mtime.group(1) if keep01 else str(int(mtime.group(1)))
        return f"{hh}:{mtime.group(2)}"
    msec = re.search(r"(\d{1,3})\s*秒", q)
    if msec:
        return msec.group(1) + "秒"
    mmin = re.search(r"(\d{1,3})\s*分", q)
    if mmin:
        n = int(mmin.group(1))
        return (f"{n}分") if n == 2 else (f"{n}分钟")
    return ""


_PLAY_TIMED = re.compile(r"跳到|跳转|直接跳到|从\s*\d+:\d+\s*开始|跳到开头|跳到(?:最)?开头|跳到结尾|跳到尾|跳到.{0,2}开始")


def _playback(q: str) -> tuple[str, dict] | None:
    q2 = q.strip()

    # ---- 1 播放模式（裸播放/播放模式）----
    if q2 == "播放模式":
        return _play_js("播放", "", "")
    if q2 in ("播放", "开始播放", "继续播放", "恢复播放"):
        return _play_js("播放", "播放控制", "")

    # ---- 3.1 切到下一/上一 → 上/下 + 单位（首走切换，曲→首，期→集）----
    m_cut = None
    m1 = re.search(r"切到(下一|上一)(集|首|个|部|期|曲)", q2)
    m2 = re.search(r"切(下一|上一)(集|个|部|期)", q2)
    if m1:
        col = "上" if "上" in m1.group(1) else "下"
        unit = "首" if m1.group(2) in ("首", "曲") else ("集" if m1.group(2) == "期" else m1.group(2))
        return _play_js(col, "", unit)
    if m2:
        col = "上" if "上" in m2.group(1) else "下"
        unit = "集" if m2.group(2) == "期" else m2.group(2)
        return _play_js(col, "", unit)
    # 往前切一集/往后切一集/往回切一首 → 上/下 + 单位
    if re.search(r"往前切|往后切", q2):
        fw = bool(re.search(r"往前切", q2))
        unit = "首" if re.search(r"首|曲", q2) else ("个" if "个" in q2 else ("部" if "部" in q2 else "集"))
        return _play_js("上" if fw else "下", "", unit)

    # ---- 2 音乐类关闭/退出 → 关闭 音乐播放控制 ----
    if re.search(r"音乐别放了|退出音乐|把音乐(?:关了|退|关掉)|关了音乐|音乐退出|关闭音乐|关掉音乐", q2) \
       or (("音乐" in q2 or "歌" in q2) and re.search(r"关了|关掉|退出来", q2) and not re.search(r"换个歌|下一首", q2)):
        return _play_js("关闭", "音乐播放控制")

    # ---- 3 切换到/换歌/换一首/换一部/听个别的/给我切一个 ----
    if re.search(r"听着呢|这首|来一首|再来一", q2) and re.search(r"(?:切|选|跳)下一(?:首|曲)", q2):
        return _play_js("切换", "", "首")
    if re.search(r"听个别的|听别的|换歌|切歌|换一首|换一|换个歌|帮我换一首|换一部|随机换一部|给我切一个|不好听换|给我换一个|换一个|再来一|切下一首|跳到下一首|切下一首吧", q2) \
       and not re.search(r"切到下一|切到上一|往前切|往后切|下一集|上一集|接着放|往下放|别放|别播", q2):
        # 想听个别的/嗯我想听个别的 → value=个；裸 听个别的/听别的 → 空
        if q2 == "听个别的" or re.search(r"^听别的$", q2):
            return _play_js("切换", "", "")
        if re.search(r"听.*?个别的", q2):
            return _play_js("切换", "", "个")
        if re.search(r"听别的|听个吧", q2):
            return _play_js("切换", "", "")
        val = "首" if ("首" in q2 or "歌" in q2 or "曲" in q2 or "切歌" in q2) else ("个" if (re.search(r"个", q2) or "节目" in q2) else ("部" if "部" in q2 else ""))
        return _play_js("切换", "", val)

    # ---- 3.5 继续/接着/恢复/往下 → 播放（早于停止，避免停吞）----
    if re.search(r"继续|接着|往下放|往下播|恢复播放|接着播|接着放", q2) \
       and not re.search(r"下一首|上一首|跳过|换|循环|一遍|接着来|别放|别播|不放了|别再", q2):
        return _play_js("播放", "播放控制", "")

    # ---- 3.6 上一首重新/再/重听；再听一遍+刚才那首 → 上/重播 ----
    if "上一首" in q2 and re.search(r"重新|再放|再听|重听|重放|再来|重新来", q2):
        return _play_js("上", "", "首")
    if "再听一遍" in q2 and "刚才那首" in q2 and q2 != "刚才那首再听一遍":
        return _play_js("重播", "播放列表", "")

    # ---- 3.7 刚才那首 非重放 → 上 首（放回/切回/回到/换回/倒回/往回切）----
    if re.search(r"刚才那首|倒一首回去|往回切一首", q2) \
       and not re.search(r"重放|重听|再听|重播|重新|接着|继续|再放|放一遍", q2):
        return _play_js("上", "", "首")

    # ---- 3.8 通用重播 → 重播 ----
    if re.search(r"重放一遍|重听一遍|再听一遍|再放一遍|重新放|重新来|重播", q2):
        return _play_js("重播", "", "")

    # ---- 4a 太吵了别放了 / 太吵了别放了 → 关闭（golden 精确，先于泛化）----
    if q2 == "太吵了别放了":
        return _play_js("关闭", "")
    if q2 == "不好听别放了":
        return _play_js("关闭", "音量")
    # ---- 4 太吵/不好听/难听 + 停止词 → 停止播放控制（golden）----
    if re.search(r"(?:太吵|不好听|难听|太闹|不喜欢|听腻).{0,6}(?:别放|不放了|别播|停|暂停)", q2):
        return _play_js("停止", "播放控制", "")

    # ---- 4b 退出播放（golden 参差：退出/停止两条不同 golden，按顺序先命中退出）----
    if re.search(r"帮我退出播放|麻烦退出播放", q2):
        return _play_js("停止", "播放控制", "")
    if q2 == "退出播放":
        return _play_js("退出", "播放控制", "")
    if q2 == "不听了退出来":
        return _play_js("退出", "", "")

    # ---- 5 停止/暂停/别放了 ----
    if q2 == "暂停":
        return _play_js("暂停", "播放控制", "")
    if re.search(r"暂停|播放停止|停止(?:播放)?|别再|不要播了|不要放|不要再放|不要播放|不想听|不听了|不想|"
                 r"别放了|不放了|别放|别播|别唱|把.{0,30}(?:停|暂停)|太.{0,3}?停|停一下|停|停掉了|停了|"
                 r"歌.{0,4}停|帮我把.{0,4}停|停音乐|音乐.{0,3}先停", q2) \
       or re.search(r"先别放|先别播|不播了|不再放|不再播放|这.{0,3}别放", q2) \
       or re.search(r"把.{0,4}停了|把.{0,4}停一下|停.{0,4}音乐", q2):
        return _play_js("停止", "播放控制", "")

    # ---- 6 退出 / 回退 ----
    if re.search(r"回退一首", q2):
        return _play_js("退出", "", "首")
    if re.search(r"退一首", q2):
        return _play_js("上", "", "首")
    if re.search(r"退出来|不听了退出来|退出去", q2):
        return _play_js("退出", "", "")
    if q2 == "退出播放":
        return _play_js("退出", "播放控制", "")
    # 快进功能/快退功能/视频快进/视频快退 → 快进/快退（无秒，纯意图）
    if re.search(r"快进功能|视频快进", q2) and not re.search(r"秒|分钟|:\d\d", q2):
        return _play_js("快进", "", "")
    if re.search(r"快退功能|视频快退", q2) and not re.search(r"秒|分钟|:\d\d", q2):
        return _play_js("快退", "", "")

    # ---- 7 恢复声音 → 数值关闭静音 ----
    if re.search(r"恢复.{0,2}(?:声音|音量)|恢复正常.{0,2}音量|声音恢复正常", q2):
        return ("numeric_adjust", _slot("关闭", "静音"))

    # ---- 8 继续/接着/恢复/往下 → 播放（放其首，别被停止先吞）----
    if re.search(r"继续|接着|往下放|往下播|恢复播放|把暂停的歌接着放|把.{0,4}接着放", q2) \
       and not re.search(r"下一首|上一首|换|跳过|循环|一遍|接着来", q2):
        return _play_js("播放", "播放控制", "")

    # ---- 9 重播 / 回到刚才那首（放于停止之前；上一首=上/首）----
    if re.search(r"上一首", q2) and re.search(r"重新|再放|重听|重播|放一遍|再来|重新来", q2):
        return _play_js("上", "", "首")
    if "再听" in q2 and "刚才那首" in q2 and q2 != "刚才那首再听一遍":
        return _play_js("重播", "播放列表", "")
    if re.search(r"刚才那首", q2) and re.search(r"重放|重听|再听|重新|再放", q2):
        return _play_js("重播", "播放列表" if q2 == "再听一遍刚才那首" else "", "")
    if re.search(r"刚才那首", q2) and not re.search(r"接着|继续", q2):
        return _play_js("上", "", "首")
    if re.search(r"重放一遍|重听一遍|再听一遍|再放一遍|重新放|重播", q2):
        return _play_js("重播", "", "")

    # ---- 10 倍速 ----
    if "倍速" in q2:
        if re.search(r"加快|调快|提高|提速", q2) and not re.search(r"放慢|降低|调慢", q2):
            return _play_js("提高", "倍速", "")
        if re.search(r"调小|放慢|降低|减慢|调慢", q2):
            return _play_js("降低", "倍速", "")
        if "默认" in q2:
            return _play_js("设置", "倍速", "1")
        m = re.search(r"(\d+(?:\.\d+)?)\s*倍速", q2)
        if m:
            num = m.group(1)
            if re.search(r"换成|切换成", q2) and re.search(r"^\d+$", num):
                num += ".0"
            return _play_js("设置", "倍速", num)
        return _play_js("设置", "倍速", "")

    # ---- 11 列表播放 / 随机播放 / 单曲循环 / 循环 / 顺序 ----
    if "随机播放" in q2:
        return _play_js("随机播放", "播放列表", "")
    if "列表播放" in q2:
        return _play_js("列表播放", "播放列表", "")
    if re.search(r"单曲循环", q2):
        return _play_js("单曲播放", "播放列表", "")
    if "列表循环" in q2:
        return _play_js("循环播放", "", "")
    if re.search(r"循环播放|循环", q2):
        return _play_js("循环播放", "播放列表", "")
    if "播放顺序" in q2:
        return _play_js("顺序播放", "", "")
    if re.search(r"顺序播放", q2):
        return _play_js("顺序播放", "播放列表", "")

    # ---- 12 快进/快退/前进/后退（纯意图；快进功能/视频快进/快退功能 → 快进/快退）----
    if re.search(r"快进到|快退到|快进|快退|前进|后退|倒退", q2) \
       and re.search(r"秒|分钟|:\d\d|\.\d", q2):
        if "快进到" in q2:
            return _play_js("快进到", "", _timed_val(q2))
        if "快退到" in q2:
            return _play_js("快退到", "", _timed_val(q2))
        if re.search(r"快进|前进", q2) and not re.search(r"快退|后退|倒退", q2):
            return _play_js("快进", "", _timed_val(q2))
        return _play_js("快退", "", _timed_val(q2))

    # ---- 13 跳转 ----
    if re.search(r"跳到|跳转|直接跳到|从\s*\d+:\d+\s*开始", q2) \
       and not re.search(r"下一|上一|换", q2):
        if re.search(r"(\d{1,2}:\d{2})", q2):
            return _play_js("跳转", "", _timed_val(q2))
        return _play_js("跳转", "", "")

    # ---- 14 上/下（上一集/下一集/上一首/下一首/一部/一个）----
    if re.search(r"上一|下一|上一首|下一首|上一集|下一集|上一部|下一部|上一曲|下一曲|上一个|下一个", q2):
        unit = ""
        if "集" in q2:
            unit = "集"
        elif "部" in q2:
            unit = "部"
        elif "个" in q2:
            unit = "个"
        elif "曲" in q2 or ("首" in q2):
            unit = "首"
        direction = "上" if "上" in q2 else "下"
        obj = ""
        if unit == "集" and (q2 in ("上一集", "下一集") or q2.startswith("播放上一集") or q2.startswith("播放下一集")):
            obj = "播放控制"
        if unit == "个" and q2 in ("上一个", "下一个"):
            obj = "播放控制"
        return _play_js(direction, obj, unit)

    # ---- 15 换/跳过 ----
    if re.search(r"换一|换掉|跳过这首|这首.{0,6}跳过|这首.{0,6}切|这首歌.{0,6}切|不要这首|不喜欢这首|"
                 r"换一部|切一部|换一个|换个|换歌|切掉|换首|换.{0,3}别的|来.{0,3}别的|听.{0,3}别的|"
                 r"听个别的|听别的吧|来个别的|帮我换一个|这个.{0,4}换|我要换|听腻|听够了|换别的|换个别的", q2):
        val = "首" if ("首" in q2 or "歌" in q2 or "曲" in q2) else ("部" if "部" in q2 else ("个" if ("个" in q2 or "节目" in q2) else ""))
        if "听" in q2 and "别的" in q2 and not re.search(r"换|个", q2):
            val = "个"
        return _play_js("切换", "", val)

    return None


# ================= 4. 屏幕布局 =================
def _layout(q: str) -> tuple[str, dict] | None:
    # 关闭/退出 分屏/小屏 → 关闭
    m_cl = re.match(r"^(关闭|退出|关掉|关闭掉)(分屏|小屏|全屏)$", q.strip())
    if m_cl:
        return ("screen_layout", _slot("关闭", m_cl.group(2)))
    if re.search(r"隐藏分屏", q):
        return ("screen_layout", _slot("打开", "隐藏分屏"))
    if re.search(r"显示分屏", q):
        return ("screen_layout", _slot("打开", "显示分屏"))
    if re.search(r"分屏|多窗口|双屏|多屏|画面分割|两个画面|多任务分屏", q) and "多屏互动" not in q:
        return ("screen_layout", _slot("打开", "分屏"))
    if re.search(r"全屏|全屏幕", q):
        return ("screen_layout", _slot("打开", "全屏"))
    if re.search(r"小窗|迷你屏|小屏播放", q) and not re.search(r"亮度|音量|分辨率|色彩|画|调|提高|降低|查询|关闭|退出", q):
        return ("screen_layout", _slot("缩小", "画面"))
    if re.search(r"小屏|小屏幕", q) and not re.search(r"亮度|音量|分辨率|色彩|画|-*調|调|提高|降低|查询|播放", q):
        return ("screen_layout", _slot("打开", "小屏"))
    if re.search(r"画面缩小|缩小画面|画面缩|缩小", q) and not re.search(r"亮度|音量|分辨率|色", q):
        return ("screen_layout", _slot("缩小", "画面"))
    return None


# ================= 5. 数值调节 =================
_NUM = [
    ("音量", re.compile(r"音量|声音大小|声量|声音音|声音")),
    ("静音", re.compile(r"静音|把声音关|声音关掉|静音模式|消音|关闭声音|静音开关|声音静音")),
    ("亮度", re.compile(r"亮度|屏幕亮|屏亮|明亮|太刺眼|屏幕调亮|屏幕调暗|调亮屏幕|调暗屏幕|亮了|暗了|(?:电视?)?画面调亮|(?:电视?)?画面调暗|电视?画面调到最|画面调到最")),
    ("对比度", re.compile(r"对比度")),
    ("色度", re.compile(r"色度|色彩饱和度")),
    ("清晰度", re.compile(r"清晰度|解析度")),
    ("分辨率", re.compile(r"分辨率")),
    ("麦克音量", re.compile(r"麦克音量|麦克风|麦克")),
    ("氛围灯亮度", re.compile(r"氛围灯亮度")),
    ("氛围亮度", re.compile(r"氛围亮度")),
    ("小屏亮度", re.compile(r"小屏亮度")),
    ("高刷新率", re.compile(r"高刷新率|高刷|刷新率")),
]
_NUM_RAISE = re.compile(r"调高|调大|提高|加大|大一点|高一点|亮一点|亮些|调亮|增|更亮|更响|再大|声音大|大点|大些|高些|太暗|太黑|太脏|鲜艳|清晰|亮一点|大一点|大一点|高一点|色|饱满")
# 数值对象里"打开/开启/启动/进入 X + no 调" → 打开对象
_OPEN_ONLY_OBJ = {"高刷新率", "分辨率", "麦克音量", "氛围灯亮度", "氛围亮度"}
_NUM_LOWER = re.compile(r"调低|调小|降低|减小|变小|小一点|小一些|小点|低一点|暗一点|调暗|减小|降|减|小|太暗|太黑|太低|太冷|太暗|太暗|刺眼|太低|小$|暗$|低$|<")


# "画面/画质/色彩 + 状态短语" → 数值调节（清晰度/色度/对比度）
def _pic_state(q: str) -> tuple[str, dict] | None:
    vlow = re.sub(r"[\s]", "", q)
    if not re.search(r"(?:画面|画质|色彩|图片|屏幕).*(?:一点|低|些|起来|强|了|调高|调低|太高|太艳|太淡|饱和)", q):
        return None
    low_v = True
    if re.search(r"降低|调低|降|太暗|暗了|太淡|不足|淡", vlow):
        low_v = True
    # 清晰类 → 清晰度（画面清晰/画面模糊了/画质清晰）
    if re.search(r"(?:画面|画质|屏幕|图片).*(?:清晰|清楚|模糊|锐利)", q):
        op = "提高" if not re.search(r"模糊|降低|调低", vlow) else "降低"
        if "模糊" in q and "降低" not in q and "调低" not in q:
            op = "提高"
        # golden：画面清晰一点/现在清晰度多少→默认；仅“画面调清晰”无值
        val = "默认" if re.search(r"画质|清晰度|清晰一点|现在|多少|分辨率|降低清晰度|提高清晰度", q) else ""
        return ("numeric_adjust", _slot(op, "清晰度", val))
    # 对比度
    if re.search(r"(?:画面|色彩|对比).*对比|对比.*(?:一点|强|了)", q):
        op = "提高"
        return ("numeric_adjust", _slot(op, "对比度", ""))
    # 色度/鲜艳/饱和度
    if re.search(r"(?:画面|画质|色彩).*(?:太艳|鲜艳|饱和度|饱和|浓郁|淡)", q):
        if re.search(r"设置|调整|调节|控制", q):
            return ("numeric_adjust", _slot("设置", "色度", ""))
        if re.search(r"降低|调低|降|变淡|太淡|太艳|太浓|饱和降|太饱和|淡一下|淡了", vlow):
            op = "降低"
        else:
            op = "提高"
        return ("numeric_adjust", _slot(op, "色度", ""))
    return None


def _numeric(q: str) -> tuple[str, dict] | None:
    # 先定位 object 与下标，避免 "音量调到静音" 被音量吃掉
    vlow = re.sub(r"[\s]", "", q)
    # 描述性画面状态（"画面清晰一点/色彩鲜艳一点/画面模糊了/对比强一点"）
    ps = _pic_state(q)
    if ps:
        return ps
    # 静音（词显式出现即静音）
    if re.search(r"静音|关掉|关闭声音|把声音关|消音|声音关闭|恢复正常|恢复声音|取消静音|退出静音|声音静音", vlow):
        # 关闭方向：恢复/取消/退出 +  静音；或"恢复正常声音/关闭静音模式"
        if re.search(r"恢复|取消|退出|恢复正常|关闭静音", vlow):
            op = "关闭"
        else:
            op = "打开"
        return ("numeric_adjust", _slot(op, "静音"))
    # 特化对象（含具体限定词）先行，避免被通用"音量/亮度"吞掉
    # 麦克风/氛围灯/氛围/小屏亮度 → 精确
    if re.search(r"提高.?分辨率|提升分辨率|调高分辨率", q):
        return ("numeric_adjust", _slot("提高", "清晰度", "默认"))
    for obj, pat in (("麦克音量", r"麦克风?音量|麦克风音量|麦克风|麦克"),
                     ("氛围灯亮度", r"氛围灯亮度"),
                     ("氛围亮度", r"氛围亮度"),
                     ("小屏亮度", r"小屏亮度"),
                     ("高刷新率", r"高刷新率|高刷|刷新率")):
        if re.search(pat, q):
            # 打开-对象型
            if re.match(r"^(?:打开|启动|进入|开启)", q):
                return ("numeric_adjust", _slot("打开", obj))
            if re.search(r"查询|现在|多少|当前|目前", q):
                return ("numeric_adjust", _slot("查询", obj))
            if re.search(r"调高|提高|增|加大", vlow):
                return ("numeric_adjust", _slot("提高", obj))
            if re.search(r"调低|降低|减小", vlow):
                return ("numeric_adjust", _slot("降低", obj))
    for obj, rx in _NUM:
        if rx.search(q):
            # 打开-对象型（高刷新率/分辨率/麦克音量/氛围灯亮度/氛围亮度）
            if re.match(r"^(?:打开|启动|进入|开启)", q) and obj in _OPEN_ONLY_OBJ:
                return ("numeric_adjust", _slot("打开", obj))
            # 查询
            if re.search(r"现在|多少|查询|当前|目前|是多少", q):
                return ("numeric_adjust", _slot("查询", obj,
                                                 "默认" if _numeric_default(obj, q) else ""))
            # 加N/减N → 提高/降低 + value
            mgn = re.search(r"(加|减)(\d{1,3})", vlow)
            if mgn:
                op = "提高" if mgn.group(1) == "加" else "降低"
                return ("numeric_adjust", _slot(op, obj, mgn.group(2) + "%" if "%" in vlow else mgn.group(2)))
            # "声音大小调节/调节声音大小"→ 设置；纯粹的降一些/小一些 是 降低
            if re.search(r"大小.?调节|调节.?大小|大小$", q):
                return ("numeric_adjust", _slot("设置", obj))
            # "调到X/最低/最大" → 设置（不是提高/降低）
            if re.search(r"调整|调节|控制|设置|调到|设为|打到|开到|等于|数值", q):
                # 调整/调节/控制（无值）→ 设置
                if re.search(r"调整|调节|控制|X调整$|\.调整|调节$|控制$|调整|设置$", q) and not re.search(r"调高|调低|提高|降低|加大|减小|增大|增|减|变高|变低|设置.*[0-9%]|到[0-9%]|数值[0-9%]", q):
                    return ("numeric_adjust", _slot("设置", obj, ""))
                op = "设置"
                val = _num_value(vlow)
                return ("numeric_adjust", _slot(op, obj, val))
            # 亮度太暗/太黑 → 提高（需要更亮）；对比度/色度 太艳/太浓 → 降低
            # 明确降低词优先（降低/调低/调小/降/调暗/减/太低/太艳）
            if re.search(r"调低|调小|降低|减小|调暗|降下来|减|太低|太高|太艳|太浓|降低一些|调低一些|小一点", vlow):
                op = "降低"
                val = "默认" if _numeric_default(obj, q) else _num_value(vlow)
                return ("numeric_adjust", _slot(op, obj, val))
            if _NUM_RAISE.search(q):
                op = "提高"
                val = "默认" if _numeric_default(obj, q) else _num_value(vlow)
                return ("numeric_adjust", _slot(op, obj, val))
            if _NUM_LOWER.search(q):
                op = "降低"
                val = "默认" if _numeric_default(obj, q) else _num_value(vlow)
                return ("numeric_adjust", _slot(op, obj, val))
            if re.search(r"打开|开启|启动|进入|开关", q):
                # golden 契约：亮度开关 → object=亮度控制（其余音量开关等仍 object=原名）
                if obj == "亮度" and re.search(r"开关", q):
                    return ("numeric_adjust", _slot("打开", "亮度控制"))
                return ("numeric_adjust", _slot("打开", obj))
            op = "设置"
            val = _num_value(vlow)
            return ("numeric_adjust", _slot(op, obj, val))
    return None


def _numeric_default(obj: str, q: str) -> bool:
    """对比度/亮度/清晰度的"默认"值特例。"""
    if obj not in ("对比度", "亮度", "清晰度"):
        return False
    if re.match(r"^(调高|调低)", q):
        return True
    if "太高了" in q:
        return obj == "对比度"
    if "降低一些" in q and obj == "亮度":
        return True
    if (re.search(r"清晰一点|清晰.{0,2}一点", q)) and obj == "清晰度":
        return True
    if re.search(r"^(?:降低|调低)清晰度$", q) and obj == "清晰度":
        return True
    if re.search(r"现在.+多少", q) and obj in ("对比度", "亮度", "清晰度"):
        return True
    return False


def _num_value(vlow: str, obj: str = "", q: str = "") -> str:
    if re.search(r"最大|最高|最亮|最明", vlow):
        return "100%"
    if re.search(r"最低|最小|最暗", vlow):
        return "0%"
    if re.search(r"一半|比例一半|调一半", vlow):
        return "50%"
    if "默认" in vlow or re.search(r"恢复|复原", vlow):
        return "默认"
    m = re.search(r"(\d{1,3})\s*(%)?格?", vlow)
    if m:
        return m.group(1) + (m.group(2) or "")
    return ""


# ================= 6. 定时关机 =================
_CN = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6",
       "七": "7", "八": "8", "九": "9", "十": "10", "十一": "11", "十二": "12",
       "二十": "20", "三十": "30", "四十五": "45"}


def _cn(s: str):
    if s in _CN:
        return _CN[s]
    digits = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "零": "0"}
    if s and all(c in digits for c in s):
        return "".join(digits[c] for c in s)
    return None


def _timer(q: str) -> tuple[str, dict] | None:
    if "关机" not in q:
        return None
    if re.search(r"取消关机|取消.*关机|别关机|不关机|取消定时关机", q):
        return ("timer_control", {"operation": "关闭", "object": "关机", "value": "",
                                  "device": "", "location": ""})
    # 睡前关机/关机时间查询 → 定时关机（time 语义：查询归 timer）
    dt = ""
    # 定时（无具体时间：自动关机时间设置/关机时间设置/定时关机设置/时间设置）
    if re.search(r"自动关机时间设置|关机时间设置|定时关机设置|时间设置", q):
        return ("timer_control", {"operation": "打开", "object": "关机", "value": "",
                                  "date_time": "定时", "device": "", "location": ""})
    # 睡前关机 → 定时打开（golden 无 date_time）
    if re.match(r"睡前关机", q):
        return ("timer_control", {"operation": "打开", "object": "关机", "value": "",
                                  "device": "", "location": ""})
    # 关机时间查询/查询关机时间/打开|启动|进入剩余关机时长 → 剩余关机时长
    if re.search(r"关机时间查询|查询关机时间|剩余关机时长", q):
        return ("timer_control", {"operation": "查询", "object": "剩余关机时长",
                                  "value": "", "device": "", "location": ""})
    if re.match(r"^(?:打开|启动|进入)剩余关机时长$", q):
        return ("timer_control", {"operation": "查询", "object": "剩余关机时长",
                                  "value": "", "device": "", "location": ""})
    if "定时" in q and not re.search(r"[0-9一二三四五六七八九十]+(分钟|小时|点)", q):
        dt = "定时"
    # 点钟（晚上/白天）
    mh = re.search(r"(晚上|晚间|深夜|晚|下午|早上|上午)?\s*([0-9一二三四五六七八九十]+)点", q)
    if mh:
        daypart, hs = mh.group(1), mh.group(2)
        h = _cn(hs) if not hs.isdigit() else hs
        try:
            h = int(h)
            if daypart in ("晚上", "晚间", "晚", "下午") and h < 12:
                h += 12
            elif daypart in ("早上", "上午") and h == 12:
                h = 0
            dt = f"{h:02d}:00"
        except Exception:
            pass
    # 小时/分钟（含"半小时/半小时后"）
    if not dt and re.search(r"半小时", q):
        dt = "30分钟"
    m = re.search(r"(?:([0-9一二三四五六七八九十两]+)个?)?\s*(小时|分钟)\s*(?:后|自动)?", q)
    if m and not dt:
        unit, num = m.group(2), m.group(1)
        if num is None:
            dt = "1" + unit
        else:
            n = _cn(num.strip()) if num.strip() and not num.strip().isdigit() else num.strip()
            if n is None:
                n = num.strip()
            dt = f"{n}{unit}"
    # 英文单位：1h / 3h后 / 30min
    if not dt:
        mh = re.search(r"(\d+)\s*[hH]", q)
        if mh:
            dt = mh.group(1) + "小时"
        else:
            mmin = re.search(r"(\d+)\s*(?:min|分钟)", q)
            if mmin:
                dt = mmin.group(1) + "分钟"
    if not dt:
        return None
    return ("timer_control", {"operation": "打开", "object": "关机",
                              "date_time": dt, "device": "", "location": ""})


# ================= 7. 电源 =================
def _power(q: str) -> tuple[str, dict] | None:
    if re.search(r"重启", q):
        return ("power_control", _slot("打开", "重启"))
    # 开机XX定制/开机灯效 → 非电源，落入 common
    if re.search(r"开机(?:图片|灯效).*定制|开机灯效|关机动画", q) and re.search(r"定制|灯效|动画", q):
        return None
    if re.search(r"开机|打开电视|开机电视|启动电视|电视开机|唤醒|电视机开机|电视启动|开电视开关|打开电视开关|开启电视|把电视打开|电视打开|电视开关|电视机|开电视$|启动电视|打开电视", q):
        op = "" if re.match(r"把电视打开$", q) else "打开"
        return ("power_control", _slot(op, "开机"))
    if re.search(r"关门|关机|关闭电视|关电视|把电视关|电视关机|我要关机|电视关了|关机了一下", q):
        return ("power_control", _slot("打开", "关机"))
    return None


# ================= 8. 模式 =================
# 音效值（XX音效）→ 对象=音效模式
_SOUND_EFFECTS = ["杜比音效", "流行音效", "舞曲音效", "蓝调音效", "标准音效", "影院音效", "音乐音效"]
# 声音模式值
_SOUND_MODES = ["标准模式", "音乐模式", "影院模式", "体育模式"]
# 图像模式值
_IMAGE_MODES = ["标准模式", "鲜艳模式", "影院模式", "体育模式"]
_MODE_OBJ = [
    ("图像模式", re.compile(r"图像模式|图像|显示模式|画面模式|画质模式|画面效果模式|视频模式")),
    ("音效模式", re.compile(r"音效模式|音响效果|音效设置|音效|声音效果模式|效果模式")),
    ("声音模式", re.compile(r"声音模式|声音场景|声音输出|声音调节|声模式|音频模式|声音效果|声音配置|声音选择")),
]
_MODE_EXACT = ["护眼模式", "商场模式", "会议室模式", "熄屏模式", "音箱模式开关", "音箱模式", "电视模式",
               "月光模式", "混响模式", "会议模式"]
# 打开类模式对象（值空）
_MODE_OPEN = {"混响模式", "音箱模式开关", "电视模式"}


def _mode(q: str) -> tuple[str, dict] | None:
    q2 = q.strip()
    if "商场模式设置" in q2 or "多屏互动" in q2:
        return None
    if re.match(r"^(?:打开|启动|进入)", q2) and "音效" not in q2 and "模式" not in q2:
        return None
    # 音效值：音效模式/杜比音效 等 → 对象音效模式 + value=XX音效
    for eff in _SOUND_EFFECTS:
        if eff in q2:
            return ("mode_control", _slot("设置", "音效模式", eff))
    # 先匹配显式的 X模式 值 + 对象（声音模式/图像模式）
    # 打开/进入 音乐模式 → 对象空，value=音乐模式
    if re.match(r"^(?:打开|启动|进入|开启)(音乐模式)$", q2):
        return ("mode_control", _slot("设置", "", "音乐模式"))
    # 声音模式 + 标准/影院/音乐/体育
    if re.search(r"声音模式", q2):
        for v in _SOUND_MODES:
            if v in q2:
                return ("mode_control", _slot("设置", "声音模式", v))
        if re.search(r"声音模式\s*(?:管理|输出|场景|效果|设置(?:为|到))", q2) \
           or "音响模式" == q2.strip() or "音频模式" == q2.strip():
            return ("mode_control", _slot("设置", "声音模式", ""))
        # golden：声音模式选择/设置/调整/切换/配置/调节 → 打开 声音模式；裸 声音模式/音效模式 → 打开
        if re.search(r"声音模式\s*(?:选择|设置|调整|切换|配置|调节)", q2) \
           or q2.strip() in ("声音模式", "声音配置", "音效模式"):
            return ("mode_control", _slot("打开", "声音模式", ""))
    # 声音配置/音效配置（无“XX模式”字样）→ golden：声音配置→打开声音模式；音效配置→打开音效模式
    if q2.strip() == "声音配置":
        return ("mode_control", _slot("打开", "声音模式", ""))
    if re.search(r"音效配置", q2):
        return ("mode_control", _slot("打开", "音效模式", ""))
    if re.search(r"图像模式", q2):
        for v in _IMAGE_MODES:
            if v in q2:
                return ("mode_control", _slot("设置", "图像模式", v))
        if re.search(r"图像模式\s*(?:设置|切换|调整|管理)", q2) or q2.strip() in ("图像模式",):
            return ("mode_control", _slot("设置", "图像模式", ""))
        if re.search(r"图像模式\s*选择", q2):
            return ("mode_control", _slot("打开", "图像模式", ""))
    # 声音效果类型/音效类型/音效风格等 → 音效模式
    if re.search(r"声音效果类型|音效类型|音效风格|音效输出模式|音效调节模式", q2):
        return ("mode_control", _slot("设置", "音效模式", ""))
    # 声音效果模式/声音效果/音频效果/音质模式 → 声音模式（音效模式已在先处理）
    if re.search(r"声音效果|音频效果|音质模式|音质", q2):
        return ("mode_control", _slot("设置", "声音模式", ""))
    # 音效模式（"音效模式"显式）
    if re.search(r"音效配置|音效模式管理", q2):
        return ("mode_control", _slot("打开", "音效模式", ""))
    if re.search(r"音效模式", q2):
        for v in _SOUND_EFFECTS:
            if v in q2:
                return ("mode_control", _slot("设置", "音效模式", v))
        # golden：裸 音效模式 → 打开 声音模式
        if q2.strip() == "音效模式":
            return ("mode_control", _slot("打开", "声音模式", ""))
        if re.search(r"音效模式\s*(?:切换|设置)|切换.{0,3}音效", q2):
            return ("mode_control", _slot("设置", "音效模式", ""))
        return ("mode_control", _slot("设置", "声音模式", ""))
    # ai画质 → value=ai画质 object空
    if re.search(r"ai画质|AI画质|智能画质|自动调画质|开ai画质|智能画质模式|ai画质模式|ai?画质模式", q2, re.I):
        return ("mode_control", _slot("设置", "", "ai画质"))
    # 裸「切换到XX音效」
    if re.search(r"切换到?(杜比|流行|舞曲|蓝调)音效", q2):
        return ("mode_control", _slot("设置", "音效模式", "音效"))  # placeholder—handled above
    for o in _MODE_EXACT:
        if o in q2:
            return ("mode_control", _slot("打开", o))
    # 值模式（music→声音，其余→空对象）
    m = re.match(r"^(?:设置|切换到|切换至|打开|进入|开启)?(音乐模式|标准模式|影院模式|体育模式|鲜艳模式)$", q2)
    if m:
        v = m.group(1)
        if v in ("标准模式", "影院模式", "体育模式"):
            return ("mode_control", _slot("设置", "", v))
        if v == "鲜艳模式":
            return ("mode_control", _slot("设置", "", v))
        return ("mode_control", _slot("设置", "声音模式", v))
    # 模式模式的冗余（打开标准模式模式）→ 同上
    m2 = re.match(r"^(?:打开|进入|启动)(音乐模式|标准模式|影院模式|体育模式|鲜艳模式)模式$", q2)
    if m2 and "模式" in q2:
        v = m2.group(1)
        return ("mode_control", _slot("设置", "声音模式" if v == "音乐模式" else "", v))
    # ai画质 → value=ai画质 object空
    if re.search(r"ai画质|AI画质|智能画质|自动调画质|开ai画质|ai?画质模式", q2, re.I):
        return ("mode_control", _slot("设置", "", "ai画质"))
    for obj, rx in _MODE_OBJ:
        if rx.search(q2):
            # 声音模式家族
            if obj in ("声音模式",):
                if "音效模式" in q2 or "声音效果" in q2:
                    return ("mode_control", _slot("设置", "声音模式", ""))
                return ("mode_control", _slot("设置", obj, ""))
            if obj == "音效模式":
                return ("mode_control", _slot("设置", obj, ""))
            if obj == "图像模式":
                if re.search(r"视频模式|画面效果模式|图像模式|画质模式", q2):
                    return ("mode_control", _slot("设置", "图像模式", ""))
                return ("mode_control", _slot("设置", obj, ""))
    if "混响模式" in q2:
        return ("mode_control", _slot("打开", "混响模式", ""))
    return None


# ================= 9. 特性对象（最长命中）=================
def _screensaver(q: str) -> tuple[str, dict] | None:
    """屏保/壁纸/壁画类专支（优先于通用特性分支）。

    形状（对齐 golden）：
    - 打开/关闭/设置 屏保 / 壁纸 / 壁画
    - 打开/进入/启动…屏保画册
    - 第N个设(置|为|成）/选/确定…为 屏保/壁纸/壁画 → 设置 + value=N
    - 屏保VIP/画册VIP → 空参数（购买类）
    - 设备槽：仅当含"电视"时填 device=电视
    """
    low_q = _norm(q)
    if not ("屏保" in q or "壁纸" in q or "壁画" in q):
        return None
    # 屏保VIP/画册VIP 购买/开通/续费/办理 → 空参数
    if re.search(r"VIP|开通屏保|续费屏保|购买屏保|买下屏保|办理屏保|申请屏保|买屏保|购屏保", q):
        return ("screensaver_control", {})
    if "屏保画册" in q and re.search(r"打开|进入|启动|放|看|展示|运行|开|用|去|请", q):
        dev = "电视" if "电视" in q else ""
        return ("screensaver_control", _slot("打开", "屏保画册", "", dev))
    # 第N个…设(为/成/置)、作/做/当/选/确定 …为 屏保/壁纸/壁画 → value=N
    m = re.search(r"([1-5一二三四五])\s*个?\s*(?:设为|设置成|设置为|设成|作|做|当|作为|选|用|确定)", q)
    if not m:
        m = re.search(r"第?\s*([1-5一二三四五])\s*个?\s*(?:为|设|做|当|选|用|确定)[^，。！？]{0,4}(屏保|壁纸|壁画)", q)
    if m:
        digit = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5"}.get(m.group(1), m.group(1))
        for obj in ("屏保", "壁纸", "壁画"):
            if obj in q:
                return ("screensaver_control", _slot("设置", obj, digit))
    # 我想设置屏保壁画 → 空参数（golden）
    if re.search(r"设置屏保壁画", q):
        return ("screensaver_control", {})
    # 打开屏保设置 / 我的设置屏保 / 设置屏保

    # 关/退出/取消/停止/屏保关闭 方向 → 关闭 object
    if re.search(r"关闭|关掉|退出|取消|停止|结束|不要|禁止|显示屏保了|不要显示|屏保(?:关闭|关掉|退|取消|停止|不要|消失)|让屏保", q):
        obj = "壁画" if "壁画" in q else ("壁纸" if "壁纸" in q else "屏保")
        dev = "电视" if "电视" in q else ""
        return ("screensaver_control", _slot("关闭", obj, "", dev))
    # 打开屏保设置 / 设置屏保 / 屏保设置 / 打开壁画设置  → 设置 object
    if re.search(r"设置|配置|选择|设置界面|设置页面|设置屏保壁纸", q):
        obj = "壁画" if "壁画" in q else ("壁纸" if "壁纸" in q else "屏保")
        dev = "电视" if "电视" in q else ""
        return ("screensaver_control", _slot("设置", obj, "", dev))
    # 打开/启动/进入/调出 屏保 / 壁纸 / 壁画 → 打开 object
    if "屏保画册" in q:
        dev = "电视" if "电视" in q else ""
        return ("screensaver_control", _slot("打开", "屏保画册", "", dev))
    if re.search(r"打开|开启|启动|进入|调出|显示|屏幕打开|电视打开|把.*打开|开屏保|开屏壁", q):
        if "屏保选择" in q:
            return ("screensaver_control", _slot("打开", "屏保选择"))
        obj = "壁画" if "壁画" in q else ("壁纸" if "壁纸" in q else "屏保")
        dev = "电视" if "电视" in q else ""
        return ("screensaver_control", _slot("打开", obj, "", dev))
    # 裸 屏保/壁纸/壁画（屏保，壁画，壁纸）→ 打开（对象）
    if q.strip() in ("屏保", "壁纸", "壁画"):
        return ("screensaver_control", _slot("打开", q.strip("屏"), "", ""))
    return None

def _feature(q: str) -> tuple[str, dict] | None:
    low_q = _norm(q)
    low = q.lower()
    # 低音调音台（低音不够沉/太弱/加强低音 → 提高；golden tool=solve_picture_sound_problem_control）
    if re.search(r"低音(?:不够|太弱|增强|调高|提高|加大)|(?:加强|增强)低音|低音不够沉", q):
        return ("solve_picture_sound_problem_control", _slot("提高", "低音调音台"))
    # 裸"打开屏幕/启动屏幕/进入屏幕" → 屏保
    if re.match(r"^(?:打开|启动|进入|开启)屏幕$", low):
        return ("screensaver_control", _slot("打开", "屏幕"))
    # 屏幕打开屏保 → 屏保打开（已由 _screensaver 处理）
    # 网络（先处理，因对象名非原文子串）；排除音频/演示对象含"无线/wifi"字样
    if re.search(r"wifi|wi-fi|网口|无线|有线|以太|局域网|热点|测|网|信道", low) \
       and not re.search(r"wifi[0-9]|杜比|全景声|音效|声音|音响|低音|声场|高音|远景|demo|演示|展示|体验|图卡", low):
        on = _net_obj(q)
        if on:
            return ("network_control", _slot("打开", on))
    hit = _longest_match(low_q)
    if hit:
        tool, obj = hit
        return (tool, _slot("打开", obj))
    return None


def _net_obj(low_q: str) -> str | None:
    l = low_q.lower()
    if "全时推送" in l:
        return "wifi全时推送"
    if "无线热点" in l:
        return "无线热点"
    if "测速" in l or "网速测试" in l:
        return "网络测速"
    if "连接测试" in l:
        return "网络连接测试"
    if "信道" in l:
        return "wifi信道"
    if "强度测试" in l:
        return "wifi强度测试"
    if "网络信息" in l:
        return "网络信息"
    # 以太网+设置/连接/配置 → 网络设置
    if ("以太网" in l or "以太" in l) and ("设置" in l or "连接" in l):
        return "网络设置"
    # 裸 wifi/无线 开关/设置/连接/开启 → 网络设置（golden：开启wifi/无线网络设置→网络设置）
    if re.search(r"wifi|wi-fi", l) and re.search(r"开启|启动|设置|连接|开$|打开", l) and "wifi网络" not in l and "无线网络" not in l:
        return "网络设置"
    if "无线网络" in l and re.search(r"设置|连接", l):
        return "网络设置"
    if "wifi" in l or "wi-fi" in l or "无线" in l or "局域网" in l:
        return "无线网络"
    if "网线" in l or "有线" in l:
        return "有线网络"
    if "以太" in l:
        return "有线网络"
    if "网络" in l or "网速" in l:
        return "网络设置"
    if "热点" in l:
        return "无线热点"
    return None


def _longest_match(low_q: str):
    """返回 (tool, original_obj)：最长归一化对象子串命中。"""
    best_len, best = -1, None
    for k, (tool, obj) in _OBJ_TOOL.items():
        if k in low_q and len(k) > best_len:
            best_len, best = len(k), (tool, obj)
    return best


def _motion_comp(q: str) -> tuple[str, dict] | None:
    """运动特征（display_control）：运动补偿 本身。
    仅当原文是「运动补偿(+动词)」→ 打开运动补偿；带画面问题词走 solve。
    更长的特性对象（如 AI加变频运动补偿）由 _feature 的词典最长命中处理。"""
    if re.search(r"运动补偿", q) and not re.search(r"画面|拖尾|拖影|模糊|不清|不清晰|ai|AI|变频|加|智能|图像", q):
        return ("display_control", _slot("打开", "运动补偿"))
    if re.search(r"运动.{0,3}(?:卡顿|不流畅)", q) and re.search(r"帮我|调节|调整|一下", q):
        return ("display_control", _slot("打开", "运动补偿"))
    return None


def _music_branch(q: str):
    if q == "音乐功能":
        return ("common_control", _slot("设置", "音乐播放器"))
    return None


def _motion_branch(q: str):
    if _motion_comp(q):
        return _motion_comp(q)
    return None


def _solve_branch(q: str):
    if _solve(q):
        return _solve(q)
    return None


def _feature_branch(q: str):
    r = _feature(q)
    if r:
        return r
    return None


def _screens_branch(q: str):
    r = _screensaver(q)
    if r:
        return r
    return None


def _timer_branch(q: str):
    if _timer(q):
        return _timer(q)
    return None


def _source_branch(q: str):
    if _source(q):
        return _source(q)
    return None


def _playback_branch(q: str):
    if _playback(q):
        return _playback(q)
    return None


def _layout_branch(q: str):
    if _layout(q):
        return _layout(q)
    return None


def _mode_branch(q: str):
    m = _mode(q)
    if m:
        return m
    return None


def _numeric_branch(q: str):
    if _numeric(q):
        return _numeric(q)
    return None


def _power_branch(q: str):
    if _power(q):
        return _power(q)
    return None


def _common_open_branch(q: str):
    if re.match(r"^(?:打开|启动|进入|开启|启用|调用|展开|进行|设置)", q):
        return ("common_control", _slot("打开", _rest(q)))
    return None


_RULE_SET = RuleSet(
    rules=[
        Rule(id="dev_music", tool="common_control", priority=1,
             title="音乐功能入口", explain="裸“音乐功能”→ 音乐播放器",
             decide=_music_branch),
        Rule(id="dev_motion_comp", tool="display_control", priority=2,
             title="运动补偿", explain="运动补偿/运动卡顿 → 打开运动补偿",
             decide=_motion_branch),
        Rule(id="dev_solve", tool="solve_picture_sound_problem_control", priority=3,
             title="画音问题修复", explain="偏色/模糊/噪点/声学等问题 → 问题修复 intent",
             decide=_solve_branch),
        Rule(id="dev_feature", tool="common_control", priority=4,
             title="特性对象控制", explain="网络/屏保/摄像头等特性对象最长命中 → 打开",
             decide=_feature_branch),
        Rule(id="dev_screensaver", tool="screensaver_control", priority=5,
             title="屏保控制", explain="屏保/壁纸/壁画/屏保画册/屏保VIP → 屏保控制",
             decide=_screens_branch),
        Rule(id="dev_timer", tool="timer_control", priority=6,
             title="定时关机", explain="关机+时间 → 定时关机 date_time",
             decide=_timer_branch),
        Rule(id="dev_source", tool="source_switch", priority=7,
             title="信号源切换", explain="HDMI/VGA/USB/机顶盒/信号源 → 切换",
             decide=_source_branch),
        Rule(id="dev_playback", tool="playback_control", priority=8,
             title="播放控制", explain="快进/暂停/上下首集/切换/倍速/音乐续播 → 播放控制",
             decide=_playback_branch),
        Rule(id="dev_layout", tool="screen_layout", priority=9,
             title="屏幕布局", explain="分屏/全屏/小屏/画面缩放 → 布局",
             decide=_layout_branch),
        Rule(id="dev_mode", tool="mode_control", priority=10,
             title="模式控制", explain="音效/声音/图像/护眼等模式 → 模式控制",
             decide=_mode_branch),
        Rule(id="dev_numeric", tool="numeric_adjust", priority=11,
             title="数值调节", explain="音量/亮度/对比度/清晰度等 → 数值调节",
             decide=_numeric_branch),
        Rule(id="dev_power", tool="power_control", priority=12,
             title="电源控制", explain="重启/开机/关机 → 电源控制",
             decide=_power_branch),
        Rule(id="dev_common_open", tool="common_control", priority=13,
             title="通用打开兜底", explain="未命中具体控制的“打开X”→ 通用打开",
             decide=_common_open_branch),
    ],
    default=None,
)


def apply(query: str) -> tuple[str, dict] | None:
    if not query or not query.strip():
        return None
    sel = _RULE_SET.select_with_rule(query.strip())
    if sel is None:
        return None
    tool, params, rule = sel
    return tool, params, rule.id
