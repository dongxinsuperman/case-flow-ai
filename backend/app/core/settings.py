from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_prefix="CASE_FLOW_",
        extra="ignore",
    )

    env: str = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8800
    database_url: str = "postgresql+asyncpg://case_flow:case_flow@127.0.0.1:55432/case_flow"
    cors_origins_raw: str = Field(
        default="http://127.0.0.1:5173,http://localhost:5173",
        validation_alias=AliasChoices("CASE_FLOW_CORS_ORIGINS", "CASE_FLOW_CORS_ORIGINS_RAW"),
    )
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # LLM 输出上限：默认优先效果，不用小 token 预算截断推理/诊断/规划结果。
    llm_max_tokens: int = 16000
    os_agent_enabled: bool = True
    public_base_url: str = "http://127.0.0.1:8800"
    aiphone_base_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias=AliasChoices("CASE_FLOW_AIPHONE_BASE_URL", "AI_PHONE_BASE_URL", "AIPHONE_BASE_URL"),
    )
    aiweb_base_url: str = Field(
        default="http://127.0.0.1:8009",
        validation_alias=AliasChoices("CASE_FLOW_AIWEB_BASE_URL", "AIWEB_BASE_URL"),
    )
    aihybrid_base_url: str = Field(
        default="http://127.0.0.1:8800/aihybrid",
        validation_alias=AliasChoices("CASE_FLOW_AIHYBRID_BASE_URL", "AIHYBRID_BASE_URL"),
    )
    aiweb_callback_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("CASE_FLOW_AIWEB_CALLBACK_BASE_URL", "AIWEB_CALLBACK_BASE_URL"),
    )
    aiapi_allowed_hosts_raw: str = Field(
        default="",
        validation_alias=AliasChoices("CASE_FLOW_AIAPI_ALLOWED_HOSTS", "CASE_FLOW_AIAPI_ALLOWED_HOSTS_RAW"),
    )
    aiapi_allowed_base_urls_raw: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CASE_FLOW_AIAPI_ALLOWED_BASE_URLS",
            "CASE_FLOW_AIAPI_ALLOWED_BASE_URLS_RAW",
        ),
    )
    aiapi_default_base_url: str = ""
    aiapi_default_headers_raw: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CASE_FLOW_AIAPI_DEFAULT_HEADERS",
            "CASE_FLOW_AIAPI_DEFAULT_HEADERS_RAW",
        ),
    )
    aiapi_allowed_methods_raw: str = Field(
        default="GET,POST,PUT,PATCH,DELETE",
        validation_alias=AliasChoices(
            "CASE_FLOW_AIAPI_ALLOWED_METHODS",
            "CASE_FLOW_AIAPI_ALLOWED_METHODS_RAW",
        ),
    )
    aiapi_allow_private_networks: bool = True
    aiapi_max_timeout_seconds: int = 20
    aiapi_max_response_bytes: int = 0
    aiapi_follow_redirects: bool = False
    function_map_context: str = ""
    function_map_context_max_chars: int = 8000
    # 诊断修复：关键失败截图落盘目录（后续可挂数据卷）；通过 /media 静态暴露。
    repair_image_dir: str = "var/repair_images"
    # 失败后台自动诊断前的等待秒数。默认 0=回调到达即视为报告就绪，立刻诊断（最简链路）。
    # 安全旋钮：若某执行器“先给 report_url、内容稍后渲染”，调成 30/60 秒避免读到半成品报告。
    auto_diagnose_delay_seconds: int = 0
    # 提交 bug 后，回写单选/多选字段前等待的秒数：飞书建单后模板会异步刷默认值，
    # 太早回写会被模板覆盖。默认 12 秒，等模板稳定再覆盖成我们的值。
    bug_field_settle_seconds: int = 12
    # 飞书项目（Meego）来源：插件鉴权凭证（敏感，仅 env，不入库/不进前端）。
    feishu_project_site_domain: str = "https://project.feishu.cn"
    feishu_project_plugin_id: str = ""
    feishu_project_plugin_secret: str = ""
    feishu_project_user_key: str = ""
    feishu_project_mcp_url: str = "https://project.feishu.cn/mcp_server/v1"
    feishu_project_mcp_token: str = ""
    # 拉哪些空间 / 时间 / 角色映射 / 状态别名等可读配置，见 backend/config/feishu_project.json。
    # AI Hybrid V1 编排防失控：到达上限后不再启动新的子工具，保留已完成证据并返回人工判断。
    hybrid_max_steps: int = 50
    hybrid_max_wall_seconds: int = 1800
    # Markdown 导入层级配置。真实文件可不提交；缺省时读取内置 legacy 8 级配置。
    markdown_import_config_path: str = "config/markdown_import.json"

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
