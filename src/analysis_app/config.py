from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required application setting: {name}")
    return value


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    apim_endpoint: str
    apim_model: str
    apim_api_version: str
    apim_token_scope: str
    apim_subscription_key: str
    sandbox_subscription_id: str
    sandbox_resource_group: str
    sandbox_group: str
    sandbox_region: str
    sandbox_disk: str
    sandbox_cpu: str
    sandbox_memory: str
    sandbox_timeout_seconds: int
    sandbox_egress_hosts: tuple[str, ...]
    outlook_mcp_url: str
    outlook_mcp_scope: str
    to_email: str
    sec_user_agent: str
    default_symbols: list[str]
    default_objective: str
    default_period: str
    default_interval: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            apim_endpoint=_required("APIM_AI_GATEWAY_ENDPOINT").rstrip("/"),
            apim_model=_required("APIM_AI_MODEL"),
            apim_api_version=os.getenv("APIM_AI_API_VERSION", "2025-04-01-preview"),
            apim_token_scope=os.getenv(
                "APIM_AI_TOKEN_SCOPE",
                "https://cognitiveservices.azure.com/.default",
            ),
            apim_subscription_key=os.getenv("APIM_SUBSCRIPTION_KEY", ""),
            sandbox_subscription_id=_required("ACA_SANDBOX_SUBSCRIPTION_ID"),
            sandbox_resource_group=_required("ACA_SANDBOX_RESOURCE_GROUP"),
            sandbox_group=_required("ACA_SANDBOX_GROUP"),
            sandbox_region=os.getenv("ACA_SANDBOX_REGION", "westus2"),
            sandbox_disk=os.getenv("ACA_SANDBOX_DISK", "stock-analysis-python"),
            sandbox_cpu=os.getenv("ACA_SANDBOX_CPU", "2000m"),
            sandbox_memory=os.getenv("ACA_SANDBOX_MEMORY", "4096Mi"),
            sandbox_timeout_seconds=int(os.getenv("ACA_SANDBOX_TIMEOUT_SECONDS", "600")),
            sandbox_egress_hosts=_csv(
                "ACA_SANDBOX_EGRESS_HOSTS",
                "pypi.org,files.pythonhosted.org,query1.finance.yahoo.com,"
                "query2.finance.yahoo.com,fc.yahoo.com,data.sec.gov,www.sec.gov,"
                "login.microsoftonline.com,*.apihub.azure.com",
            ),
            outlook_mcp_url=_required("O365_MCP_SERVER_URL"),
            outlook_mcp_scope=os.getenv(
                "O365_MCP_SCOPE",
                "https://apihub.azure.com/.default",
            ),
            to_email=_required("TO_EMAIL"),
            sec_user_agent=os.getenv(
                "SEC_USER_AGENT",
                "stock-analysis-spike contact@example.com",
            ),
            default_symbols=list(_csv("DEFAULT_STOCK_SYMBOLS", "MSFT,NVDA,AAPL")),
            default_objective=os.getenv(
                "DEFAULT_ANALYSIS_OBJECTIVE",
                "Produce an evidence-based comparative investment research report.",
            ),
            default_period=os.getenv("DEFAULT_MARKET_PERIOD", "1y"),
            default_interval=os.getenv("DEFAULT_MARKET_INTERVAL", "1d"),
        )
