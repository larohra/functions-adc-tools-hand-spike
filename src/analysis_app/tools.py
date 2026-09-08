from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field


class LocalToolExecutionBlocked(RuntimeError):
    pass


def _blocked() -> None:
    raise LocalToolExecutionBlocked(
        "Tool implementations are schemas only. SandboxFunctionMiddleware must intercept execution."
    )


@tool(
    name="get_market_history",
    description="Retrieve price and volume history plus summary statistics for one public stock.",
    approval_mode="never_require",
    max_invocations=12,
)
def get_market_history(
    symbol: Annotated[str, Field(description="Ticker symbol, for example MSFT.")],
    period: Annotated[str, Field(description="History window: 1mo, 3mo, 6mo, 1y, 2y, or 5y.")] = "1y",
    interval: Annotated[str, Field(description="Sampling interval: 1d, 1wk, or 1mo.")] = "1d",
) -> dict:
    _blocked()


@tool(
    name="get_sec_company_facts",
    description="Retrieve selected SEC company facts for a US-listed ticker.",
    approval_mode="never_require",
    max_invocations=12,
)
def get_sec_company_facts(
    ticker: Annotated[str, Field(description="US-listed ticker symbol.")],
) -> dict:
    _blocked()


@tool(
    name="calculate_portfolio_metrics",
    description=(
        "Calculate annualized return, volatility, Sharpe ratio, drawdown, and return "
        "correlations for a set of ticker symbols."
    ),
    approval_mode="never_require",
    max_invocations=4,
)
def calculate_portfolio_metrics(
    symbols: Annotated[list[str], Field(description="Two or more ticker symbols.")],
    period: Annotated[str, Field(description="History window.")] = "1y",
    interval: Annotated[str, Field(description="Sampling interval.")] = "1d",
) -> dict:
    _blocked()


@tool(
    name="execute_python",
    description=(
        "Execute LLM-generated Python in an isolated ephemeral ACA Sandbox. Use print() "
        "to return concise results. Packages are installed from PyPI inside the sandbox."
    ),
    approval_mode="never_require",
    max_invocations=8,
)
def execute_python(
    code: Annotated[str, Field(description="Complete Python program to execute.")],
    packages: Annotated[
        list[str],
        Field(description="Optional PyPI package specifications required by the program."),
    ] = [],
) -> dict:
    _blocked()


ANALYSIS_TOOLS = [
    get_market_history,
    get_sec_company_facts,
    calculate_portfolio_metrics,
    execute_python,
]
