"""运行时配置：全部走环境变量，方便容器化部署。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- LLM 网关（OpenAI 兼容）----
    api_base: str = ""            # 例如 https://gateway.example.com/v1
    api_key: str = ""             # Bearer key
    model: str = "qwen3-6-35b"
    temperature: float = 0.0
    request_timeout: float = 120.0
    max_retry: int = 3

    # ---- 工具 schema ----
    schema_dir: Path = BASE_DIR / "schema"

    # ---- 服务 ----
    host: str = "127.0.0.1"
    port: int = 8080
    service_token: str = ""       # 设置后 /predict 需要 Bearer 鉴权
    max_body_bytes: int = 64 * 1024

    @property
    def chat_url(self) -> str:
        return f"{self.api_base.rstrip('/')}/chat/completions"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
