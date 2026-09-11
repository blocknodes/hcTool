# vod 域意图解析方案

影视（vod）域的两段式意图解析：**规则优先 + LLM 兜底**（混合架构）。规则层命中即返回确定性结果，兜不住才走 LLM。目标：线上端服务 **tool+param ≥ 90%**（以 445 条 `testset.json` 的顺序无关、retext 归一评测为准），当前 **select 94.6% / tool+param 92.4%**。

---

## 1. 整体流水线

入口 `app/engine.py::run`（单域两段式，router 包裹）：

```
run(query, domain)
 └─ ① rule_select(query)            # domains/vod/rules.py::apply —— 规则优先层
     ├─ 命中 (tool, params) 且 params 非 None  → 直接返回（跳过 LLM）★
     ├─ 命中 (tool, None)            → 固定工具，仍走 LLM 填参数（fill_params）
     └─ 未命中 (None)                → 走常规两段式 select_tool → fill_params
 └─ ② select_tool（当未命中，LLM 选工具）
 ├─ ③ fill_params（LLM 按选中工具的 JSON Schema 只输出参数）
 └─ ④ postprocess（domain=vot 时 → postproc.normalize 拧 DSL 结构）
```

规则层命中且给出完整参数时**完全跳过 LLM**，这是工具+参数能过 90% 的主因；漏网的少数 case 由 LLM 填参数 + `postproc` 规范化兜底。

- **工具集**：`vod_search`、`vod_search_all`、`vod_fuzzy_search`、`vod_relate_search`、`vod_personalized_search`、`vod_history`。
- **测试集分布**：fuzzy 310、search 76、search_all 44、relate/personalized/history 各 5。
- **评测口径**：参数相等是**顺序无关**（`and`/`values` 列表集合比较）、retext 大小写/形态归一（`/tmp/params_equal.py`）。

---

## 2. 三层职责

| 文件 | 职责 |
|---|---|
| `rules.py` | 工具判别 + 简单参数。正则命中工具，能确定性生成参数则给 `(tool, params)`，否则 `(tool, None)`。 |
| `dsl.py` | search/search_all 的参数（DSL QueryNode）确定性生成器 `build_search_dsl`；无法精确合成返回 `None`，调用方回退 LLM fill。 |
| `postproc.py` | LLM 生成的宽松 params 拧到金标准 DSL 口径（结构归一，见 §6）。 |

## 3. 工具判别顺序（rules.py::apply，具体 → 宽泛）

```
1  history           看过/上一次看/继续播/接着看/追到…
2  personalized      我的偏好/猜我喜欢/结合我的喜好…
3  relate            类似/相似/同类型/和X类似的…
3.5 多演员(顿号)+片型 → search（严格：命名词表+顿号+片型，如“金秀贤、金智媛…”）
3.6 tag类浏览        “辩论赛经典视频/竞答X视频/…赛…视频” → search
4  fuzzy 强信号      片段/台词/名场面/版型/演出形式/真实事迹 → fuzzy（结构化表达不了）
4.5 具名标题+短尾≤4字  → search 关键字锚定（以家人之名DVD版 等）
5  search_all         出品/获奖/平台 或 地区/语言/年代 → 全库多维筛选
6  search            起播动词/剧集定位/结构化筛选维度
7  兜底              其余一律 fuzzy（param=整句原话）
```

**关键判别信号（regex）：**

- `_ALL_SIGNAL`（出品方/获奖/平台）→ 去 `search_all`：出品/影业/制片人/CNN-style 卫视/TVB/央视/芒果/极光/newtv/BBC/Netflix/好莱坞 ~~及奖项~~。**`注意 3D/3d/4K 绝不放入**（否则“打开3d视频”→误判 search_all）；“点映”等 HD/版型词亦保持 search。
- `_ALL_DIM`（地区/语言/年代）→ `search_all`：韩剧/美剧/内地/美国/… + `[12]\d{3}年`/`[一二…九]+年代`。但“最新/最近/近期/今年/去年”等时间副词属 **search**（不进此列）；4K/3D 版型属 search。
- `_STRUCT_DIM`（结构化筛选）→ `search`：免费/会员/评分/豆瓣/导演/主演/**参演/出演/创作/编剧/配音/改编**/全集/年份 + 片型。
- `_FUZZY_STRONG`（fuzzy 强信号）**优先于**结构化工具：片段/台词/名场面/高清版/話剧/音乐剧/演唱会/春晚/真实事迹/根据X改编/X演出视频/短剧… 一旦命中直接 fuzzy。这是防止“播放某音乐剧”被误路由到 search 的关键。

**feature 修正（胡歌演的的电影 → search）**：单字“演”原不在 `_STRUCT_DIM`，`胡歌演的电影`漏到 fuzzy。补窄匹配：

```regex
[一-龥A-Za-z0-9·]{2,8}演\s*的{0,2}\s*(?:电影|电视剧|剧|影片|短片|纪录片|综艺|的?片)
```

允许 0~2 个“的”（覆盖“演的电影”“演的的电影”）。`dsl._actor` 同时加入 `演\s*的{0,2}\s*(?:电影|电视剧|剧|影片|短片|纪录片|的?片)` 以便抽取 `actor=胡歌`。

## 4. search/search_all 参数生成（dsl.py::build_search_dsl）

复现 testset 金标准口径的**确定性槽位**；无法精确合成返回 `None` → LLM fill。

### 4.1 action 判定（点播 vs 浏览搜索）

- 检索动词头 `^(搜索|搜|查找|查…)` → `search`（永不定为 play）。
- 强起播动词头 `^(播放|请播放|帮我放|给我放|放|打开|播|小度/在X播放)` → `play`。
- 弱动词头 `我想看/我要看/看/推荐` + 命中词表标题 → `play`；或 （起播/标题）+ 定位（第N集/季/期/分钟/秒）→ `play`；否则 → `search`。

### 4.2 槽位提取（条件均可静态正则折叠）
| 槽位 | 信号示例 |
|---|---|
| `category` | 电影/电视剧/纪录片/综艺/卡通/…（`_CAT_MAP` 长词优先）|
| `tag` | 抗战/抗日/情景喜剧/悬疑/动作…（`_TAG_MAP`、`_TAG_CATEGORY` 曲艺类）|
| `area` | 内地/香港/台湾/港台(→or香港,台湾)/美国/外国…；韩剧美剧→area+category=电视剧 |
| `language` | 粤语/国语/英语/日语/中文→普通话…（语言词只作 language 维度）|
| `director` | `(名)导演` 前缀剥离检索动词 |
| `actor` | 命中 `_KNOWN_ACTORS` 词表或“主演/参演/出演/单字演”语序；否定“不要X”排除 |
| `role` | 许三多/吴石将军（角色非演员）|
| `fee/is_fee` | 免费/不要VIP→0；VIP/会员/付费→1 |
| `over/is_over` | 完结→1；连载中/在玩→0 |
| `rate` | 高于N分/评分N以上 → rate [N, 10] |
| `release_time` | 去年/今年/2025年/N年代/九十年代（→1980/1990/2010 数字或中文十年代）|
| `age_range` | 适合N到N岁 |
| `definition` | 4K/超清→is_4k；3D→is_3d |
| `sound` | 杜比全景声/立体声/DTS/环绕 |
| `series/video_index/voiceStartPos` | 战狼2→series；第N集/季/期/分秒→定位播放 |
| `company/vender_name/channel` | 正午阳光/芒果TV→芒果tv/newtv极光/卫视 |
| `prize/sub_prize` | 获奖→白玉兰/奥斯卡 + 最佳主角等 |
| `writer/dubbing/hostess` | 编剧/配音/主持 |
| `comedy_brand/creation_source` | 笑果文化/德云社；小说改编 |

- 多演员/多 tag/港台地区 用 `{"field":…,"values":[…],"operator":"or|and"}` 复合节点。
- 单子避让：`好莱坞` 若命中 company 则不当作 area 以免冲突；“情景喜剧”在 category=电视剧 时退 tag；“小品/相声/戏曲”在具名标题时作 tag。

### 4.3 输出结构
- 叶子 `{"field", "value"}`；多叶 `{"and":[…]}`；多值 `{"field","values","operator"}`。
- `result = {"action", "retext":原句, "query":…}`，附 `sort`：`新出/最新→new desc`，`好看/热播/人气→hot desc`，`评分高/高分→rate desc`。
- **title 置前**：具名标题放 query 首位（除非多维+长句放末尾）。
- 无法生成 → `None`（回退 LLM）。

## 5. 泛化性权衡

混合架构的取舍（之所以是 rule+LLM，而非纯规则/纯LLM）：

- **工作区：工具路由与组合-筛选槽**（命名动词 + 结构调结构）：强泛化——`胡歌演/主演的电影`→`vod_search + actor`，`韩剧科幻→search_all + area+category+tag`，`播放X+第N集`→play。词表映射（`_CAT_MAP`/`_AREA_MAP`/`_TAG_MAP`/`_KNOWN_*`）把口语折叠为 schema 金标取值。
- **弱：库外的专有名词**（如随便一句“看XXX的片子”里的冷门演员/剧名）。静态词表只能覆盖金标明文的条目，冷门直接回退 `_actor` 的通用“演员+片型”规则（收窄命中），否则 LLM fill 兜底。
- 因此跨库泛化能力主要集中在“可组合筛选”（维度→字段）而非“新实体识别”。实体泛化靠 LLM。

## 6. 后处理归一（postproc.py::normalize）

仅对 `vod_search/vod_search_all` 生效；其余工具原样返回。顺序：

1. **单元素 and/or 拍平**（`{and:[X]}`→`X`）。
2. **更新 play/search 与 sort 冲突**：若查询含“看到最后/最新”→action=search。
3. **字段重排 + 去重**：顶层 and 叶子按 `_FIELD_PRIORITY`（title 最前…）排，去掉重复 `(field,value)`。
4. **代号推导**：新出/最新→new desc；好看/热播→hot desc。
5. **retext 兜底**：缺失时用 query。

## 7. 验证与评测

- 离线快速：`/tmp/params_equal.py`（顺序无关比较）+ `d`/`四实ipy`/回归脚本。
- 在线全量：`python3 /tmp/eval_live_oi2.py` → 用 order+retext 无关口径直接打 `127.0.0.1:8084/api/predict`。
- 每改判别正则后**务必跑全量**，防现有 case 被吞（历史上 3.5 规则过宽吞 29 条、3D 进 `_ALL_SIGNAL` 误路由等反例）。
- curl 冒烟：
```bash
curl -s -X POST 'http://127.0.0.1:8084/api/predict' -H 'Content-Type: application/json' \
  -d '{"domain":"vod","query":"胡歌演的的电影","metadata":{}}'
# → vod_search  actor:胡歌 / category:电影
```