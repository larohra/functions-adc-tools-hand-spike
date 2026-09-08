from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_framework import FunctionInvocationContext, FunctionMiddleware
from pydantic import BaseModel

from .sandbox_executor import SandboxToolExecutor


class SandboxFunctionMiddleware(FunctionMiddleware):
    """Fail-closed boundary that replaces every local tool body with a sandbox call."""

    def __init__(self, executor: SandboxToolExecutor) -> None:
        self._executor = executor

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        del call_next
        context.result = await self._executor.execute_tool(
            context.function.name,
            _arguments_to_dict(context.arguments),
        )


def _arguments_to_dict(arguments: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(arguments, BaseModel):
        return arguments.model_dump(mode="json")
    return dict(arguments)
