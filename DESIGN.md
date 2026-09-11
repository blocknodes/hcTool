# hcTools 设计文档：LLM-first · 8 域隔离架构

## 1. 目标与核心原则

hcTools 是一个 LLM-first 的意图解析框架：给定用户问句和所属域，输出应调用的工具
及其参数。

四条核心原则：

1. **LLM-first**：工具选择与参数填充都由 LLM 完成，通过两次 function-calling 实现
   （第一次选工具，第二次填参数）。不依赖确定性规则去替模型做决策。
2. **域隔离（硬约束）**：8 个域各自一个目录，域内自成闭环。修改某个域的
   schema / prompt / few-shot / 前后处理，**不影响、不感知**其余 7 个域。
3. **域内可定制**：每个域可以有自己的前处理、后处理、few-shot、专属 prompt，但这些
   逻辑一律**局限在该域目录内**。
4. **内核稳定**：通用编排（两段式调用、网关客户端、域发现与路由）作为共享内核，
   与具体域解耦，不含任何单域特判。

## 2. 目录结构

```
hcTools/
├── app/                     # 共享内核（域无关）
│   ├── main.py              # FastAPI：/api/health、/api/predict
│   ├── config.py            # HC_* 配置（api_base / api_key / model）
│   ├── models.py            # 请求/响应模型
│   ├── llm.py               # 网关异步客户端（function calling）
│   ├── engine.py            # 两段式编排内核：select → fill
│   ├── domain.py            # Domain 契约 + 域加载器（发现 domains/*）
│   └── router.py            # 按 domain_key 分发到对应域，域间零耦合
│
├── domains/                 # ★ 8 个域，每个一个目录，互相隔离
│   ├── vod/                 # 影视
│   │   ├── __init__.py      # 导出该域的 Domain 实例（唯一必需入口）
│   │   ├── schema.json      # 该域工具定义（tool_name / description / parameters）
│   │   ├── prompt.py        # 该域 select / fill prompt（可选，缺省用内核默认）
│   │   ├── fewshot.py       # 该域 few-shot 示例（可选，第一版留空接口）
│   │   ├── preprocess.py    # 该域前处理（可选，缺省恒等）
│   │   └── postprocess.py   # 该域后处理（可选，缺省恒等）
│   ├── audio/               # 有声
│   ├── children/            # 少儿
│   ├── education/           # 教育
│   ├── sports/              # 体育
│   ├── music/               # 音乐
│   ├── device/              # 设备
│   └── fan_agent_qa/        # 泛知识
│
├── requirements.txt
├── .env.example
├── start.sh
└── DESIGN.md
```

## 3. 8 个域清单

| # | domain_key | 目录 | 中文名 |
|---|---|---|---|
| 1 | vod | `domains/vod/` | 影视 |
| 2 | audio | `domains/audio/` | 有声 |
| 3 | children | `domains/children/` | 少儿 |
| 4 | education | `domains/education/` | 教育 |
| 5 | sports | `domains/sports/` | 体育 |
| 6 | music | `domains/music/` | 音乐 |
| 7 | device | `domains/device/` | 设备 |
| 8 | fan_agent_qa | `domains/fan_agent_qa/` | 泛知识 |

## 4. 域契约（Domain Contract）

每个 `domains/<key>/__init__.py` 导出一个满足统一契约的 `Domain` 对象。内核只依赖
这个契约，**不关心域内部如何实现**——这是隔离的关键。

```
Domain:
  # ---- 必填 ----
  key: str                         # 域标识，如 "vod"
  name: str                        # 中文名，如 "影视"
  tools: list[Tool]                # 从 schema.json 加载得到

  # ---- 可选（缺省由内核提供默认）----
  select_prompt: str               # 选工具阶段 system prompt
  fill_prompt: str                 # 填参阶段 system prompt
  fewshot: list[Example]           # few-shot 示例（第一版为空列表）

  # ---- 可选钩子（缺省为恒等）----
  preprocess(req: PredictRequest) -> PredictRequest
  postprocess(tool: str, params: dict) -> dict
```

约定：

- 目录中**只有 `schema.json` 与 `__init__.py` 是必需的**；`prompt.py` / `fewshot.py` /
  `preprocess.py` / `postprocess.py` 均可缺省，内核提供合理默认。
- 钩子必须是**纯函数式**、只处理传入的本域数据，禁止读写其他域或全局可变状态。
- 前处理只能改写 query / 注入提示；不得直接决定工具或参数（那是 LLM 的职责）。

## 5. 隔离机制

| 维度 | 隔离方式 |
|---|---|
| 工具定义 | 每域独立 `schema.json`；选工具时只在本域候选内选，**不跨域**。 |
| Prompt | 每域可自带 `prompt.py`；域 A 改 prompt 与域 B 无关。 |
| Few-shot | 每域独立 `fewshot.py`，只注入本域调用。 |
| 前后处理 | 每域独立 `preprocess.py` / `postprocess.py`，只作用于本域请求。 |
| 加载 | 域加载器逐个独立加载 `domains/*/`；单域加载失败**只禁用该域**，其余 7 个正常。 |
| 运行时 | 请求带 `domain`（必填）→ router 只路由到该域 → 只执行该域流水线。域间无共享可变状态。 |

## 6. 请求处理流程（两段式 LLM）

```
POST /api/predict {query, domain, metadata?}
  │
  ├─ router 按 domain 找到对应 Domain（找不到 → 错误）
  │
  ├─ domain.preprocess(req)                       # 域内前处理（可选）
  │
  ├─ 【LLM 调用 1：选工具】
  │    engine.select(query, domain.tools,
  │                  prompt=domain.select_prompt,
  │                  fewshot=domain.fewshot)       # 只在本域工具里选，tool_choice=required
  │    → tool_name
  │
  ├─ 【LLM 调用 2：填参数】
  │    engine.fill(query, selected_tool,
  │               prompt=domain.fill_prompt,
  │               fewshot=domain.fewshot)          # 强制调用选中工具
  │    → params
  │
  ├─ domain.postprocess(tool_name, params)         # 域内后处理（可选）
  │
  └─ 响应 {tool, params}
```

说明：

- 两次调用相互独立；第一次只取工具名（忽略其参数），第二次只把选中的那一个工具交给模型。
- 第一版**不做违规校验 / 自修复**（后续可作为可选钩子加入，同样按域隔离）。

## 7. 内核 vs 域的职责边界

- **内核（app/，域无关）**：HTTP 层、两段式编排骨架、网关调用、域发现与路由、
  Tool → OpenAI function 转换。内核不含任何某个域的特判逻辑。
- **域（domains/<key>/，域相关）**：工具 schema、prompt、few-shot、前后处理。
  所有业务知识都收敛在域目录里。

## 8. API

### `GET /api/health`

```json
{ "ok": true }
```

### `POST /api/predict`

请求：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | 用户原始问句（1–2000 字符） |
| `domain` | string | 是 | 域标识（见第 3 节 domain_key），只在该域内解析 |
| `metadata` | object | 否 | 外部辅助信号，作为提示注入，不强制改写结果 |

响应：

| 字段 | 类型 | 说明 |
|---|---|---|
| `tool` | string | 选中的工具名 |
| `params` | object | 抽取的参数 |

```json
{ "tool": "playback_control", "params": { "operation": "下", "object": "播放列表" } }
```

## 9. 配置

全部走环境变量（前缀 `HC_`）：

| 变量 | 说明 |
|---|---|
| `HC_API_BASE` | LLM 网关地址（OpenAI 兼容），如 `https://gateway.example.com/v1` |
| `HC_API_KEY` | Bearer key |
| `HC_MODEL` | 模型名，默认 `qwen3-6-35b` |

启动：`./start.sh`（默认 `0.0.0.0:8084`，可用 `HC_HOST` / `HC_PORT` 覆盖）。

## 10. 落地步骤（实现顺序）

1. `app/engine.py`：从现有 predictor 抽出两段式内核，prompt / few-shot / 钩子由参数传入。
2. `app/domain.py`：定义 Domain 契约与域加载器（扫描 `domains/*`，独立加载、失败隔离）。
3. `app/router.py`：按 domain_key 分发。
4. `domains/` 下建 8 个目录骨架：每个含 `__init__.py` + `schema.json`，其余文件留空接口。
5. 从 `../../compare` 迁移 8 个域的成熟 schema（共约 45 个工具）填入各自 `schema.json`。
6. `app/main.py` 改走 router；保留 `/api/health` 与 `/api/predict`。
7. 用 mock LLM 跑通两段式流程 + 域隔离测试（含单域加载失败不影响其余域）。

## 11. 扩展一个新域的步骤

1. 在 `domains/` 下新建 `<key>/` 目录。
2. 放入 `schema.json`（工具定义）与 `__init__.py`（导出 Domain 实例）。
3. 按需添加 `prompt.py` / `fewshot.py` / `preprocess.py` / `postprocess.py`。
4. 无需改动 `app/` 任何代码——域加载器自动发现。
