"""请求/响应数据模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class PredictRequest(BaseModel):
    query: str = Field(..., description="用户原始问句", min_length=1, max_length=2000)
    domain: str = Field(
        ...,
        min_length=1,
        description="限定域标识（vod/audio/children/education/sports/music/device/fan_agent_qa）。"
        "只在该域内选工具，跳过自动路由。",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="可选：外部辅助信号，如分词/实体/维表命中。作为提示注入，不强制改写结果。",
    )

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query 不能为空")
        return v


class Prediction(BaseModel):
    domain: str = ""                      # 命中的域（内部中文名）
    tool: str = ""                        # 选中的工具名
    params: dict[str, Any] = Field(default_factory=dict)
    retried: bool = False                 # 是否触发了一次修复重试
    violations: list[str] = Field(default_factory=list)  # 最终 schema 违规项
    error: str = ""


class PredictResponse(BaseModel):
    tool: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
