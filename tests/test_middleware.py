from __future__ import annotations

import pytest
from agent_framework import FunctionInvocationContext

from analysis_app.middleware import SandboxFunctionMiddleware
from analysis_app.tools import get_market_history


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return {"source": "sandbox"}


@pytest.mark.asyncio
async def test_middleware_replaces_local_tool_execution() -> None:
    executor = FakeExecutor()
    middleware = SandboxFunctionMiddleware(executor)  # type: ignore[arg-type]
    context = FunctionInvocationContext(
        function=get_market_history,
        arguments={"symbol": "MSFT", "period": "1y", "interval": "1d"},
    )
    local_execution_called = False

    async def call_next() -> None:
        nonlocal local_execution_called
        local_execution_called = True

    await middleware.process(context, call_next)

    assert local_execution_called is False
    assert executor.calls == [
        ("get_market_history", {"symbol": "MSFT", "period": "1y", "interval": "1d"})
    ]
    assert context.result == {"source": "sandbox"}
