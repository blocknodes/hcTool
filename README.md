# hcTools — LLM-first 意图解析服务

单次 LLM function-calling 完成 **域路由 + 工具选择 + 参数抽取**。知识内聚到工具
schema（description / enum），不再依赖外部 prompt guide 和确定性后处理。

## 结构

```
hcTools/
├── app/
│   ├── main.py        # FastAPI：/api/health、/api/predict
│   ├── config.py      # HC_* 环境变量配置
│   ├── models.py      # 请求/响应模型
│   ├── predictor.py   # LLM-first 单次编排 + 一次自修复
│   ├── llm.py         # OpenAI 兼容网关异步客户端（function calling）
│   ├── registry.py    # 加载工具 schema，转 OpenAI function 定义
│   └── validate.py    # 轻量 JSON Schema 校验
├── schema/            # 工具定义（按域一个 json + index.json）
├── requirements.txt
├── .env.example
└── start.sh
```

## 运行

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 HC_API_BASE / HC_API_KEY
./start.sh             # 默认 http://127.0.0.1:8080
```

## API

健康检查：

```bash
curl -s http://127.0.0.1:8080/api/health
```

预测：

```bash
curl -s -H 'Content-Type: application/json' \
  -d '{"query":"下一集","domain":"device","metadata":{"tokens":["下","一集"]}}' \
  http://127.0.0.1:8080/api/predict
```

请求字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `query` | 是 | 用户原始问句 |
| `domain` | 否 | 域标识（vod/audio/children/education/sports/music/device/fan_agent_qa）。给定则只在该域内选工具，跳过自动路由 |
| `metadata` | 否 | 外部辅助信号（分词/实体/维表命中等），作为提示注入，不强制改写结果 |

响应：

```json
{
  "ok": true,
  "model": "qwen3-6-35b",
  "query": "下一集",
  "prediction": {
    "domain": "设备",
    "tool": "playback_control",
    "params": {"operation": "下", "object": "播放列表", "value": "集", "device": "", "location": ""},
    "retried": false,
    "violations": [],
    "error": ""
  }
}
```

## 添加工具

在 `schema/` 下新增或编辑域文件，把取值口径写进每个字段的 `description`/`enum`，
并在 `schema/index.json` 注册域文件。无需改动 `app/` 代码。
