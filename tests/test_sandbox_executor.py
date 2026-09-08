from __future__ import annotations

import json
from dataclasses import replace

import pytest

from analysis_app.config import Settings
from analysis_app.sandbox_executor import SandboxToolError, SandboxToolExecutor


class Result:
    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class FakeSandbox:
    sandbox_id = "sandbox-123"

    def __init__(self, tool_result: Result) -> None:
        self.tool_result = tool_result
        self.deleted = False
        self.files: dict[str, bytes] = {}
        self.transforms: list[dict] = []
        self.egress_policies: list = []
        self.execution_count = 0

    def exec(self, command: str) -> Result:
        if command == "true":
            return Result()
        self.execution_count += 1
        return self.tool_result

    def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def delete(self) -> None:
        self.deleted = True

    def add_egress_transform_rule(self, **kwargs) -> None:
        self.transforms.append(kwargs)

    def set_egress_policy(self, policy) -> None:
        self.egress_policies.append(policy)


class FakePoller:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self.sandbox = sandbox

    def result(self) -> FakeSandbox:
        return self.sandbox


class FakeClient:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self.sandbox = sandbox
        self.closed = False
        self.create_kwargs: dict = {}
        self.create_count = 0

    def begin_create_sandbox(self, **kwargs) -> FakePoller:
        self.create_count += 1
        self.create_kwargs = kwargs
        return FakePoller(self.sandbox)

    def list_disk_images(self) -> list:
        return []

    def close(self) -> None:
        self.closed = True


def settings() -> Settings:
    return Settings(
        apim_endpoint="https://gateway.example",
        apim_model="model",
        apim_api_version="2025-04-01-preview",
        apim_token_scope="scope",
        apim_subscription_key="",
        sandbox_subscription_id="sub",
        sandbox_resource_group="rg",
        sandbox_group="group",
        sandbox_region="westus2",
        sandbox_disk="disk",
        sandbox_cpu="1000m",
        sandbox_memory="2048Mi",
        sandbox_timeout_seconds=60,
        sandbox_egress_hosts=("pypi.org",),
        outlook_mcp_url="https://connector.example/mcp",
        outlook_mcp_scope="scope",
        to_email="user@example.com",
        sec_user_agent="test user@example.com",
        default_symbols=["MSFT"],
        default_objective="Analyze the company.",
        default_period="1y",
        default_interval="1d",
    )


@pytest.mark.asyncio
async def test_executor_deletes_sandbox_after_success() -> None:
    sandbox = FakeSandbox(Result(stdout=json.dumps({"ok": True})))
    client = FakeClient(sandbox)
    executor = SandboxToolExecutor(
        settings(),
        client_factory=lambda: client,
        connector_token_provider=lambda: "test-token",
    )

    async with executor:
        result = await executor.execute_tool("execute_python", {"code": "print(1)"})
        assert sandbox.deleted is False
        assert client.closed is False

    assert result == {"ok": True}
    assert sandbox.deleted is True
    assert client.closed is True
    assert client.create_count == 1
    assert client.create_kwargs["disk"] == "disk"
    assert client.create_kwargs["egress_policy"].default_action == "Deny"
    assert "/tmp/stock_tool_runner.py" in sandbox.files


@pytest.mark.asyncio
async def test_executor_deletes_sandbox_after_tool_failure() -> None:
    sandbox = FakeSandbox(Result(exit_code=2, stderr="boom"))
    client = FakeClient(sandbox)
    executor = SandboxToolExecutor(
        settings(),
        client_factory=lambda: client,
        connector_token_provider=lambda: "test-token",
    )

    async with executor:
        with pytest.raises(SandboxToolError, match="boom"):
            await executor.execute_tool("execute_python", {"code": "raise Exception()"})
        assert sandbox.deleted is False

    assert sandbox.deleted is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_executor_does_not_allowlist_connector_host_during_analysis() -> None:
    configured = replace(
        settings(),
        outlook_mcp_url="https://generated.connector.example/runtime/mcp",
    )
    sandbox = FakeSandbox(Result(stdout=json.dumps({"ok": True})))
    client = FakeClient(sandbox)
    executor = SandboxToolExecutor(
        configured,
        client_factory=lambda: client,
        connector_token_provider=lambda: "test-token",
    )

    async with executor:
        await executor.execute_tool("execute_python", {"code": "print(1)"})

    patterns = {
        rule.pattern for rule in client.create_kwargs["egress_policy"].host_rules
    }
    assert "generated.connector.example" not in patterns


@pytest.mark.asyncio
async def test_executor_uses_disk_id_when_preview_api_stores_name_as_label() -> None:
    sandbox = FakeSandbox(Result(stdout=json.dumps({"ok": True})))
    client = FakeClient(sandbox)
    disk = type(
        "Disk",
        (),
        {"id": "disk-id-123", "name": None, "labels": {"name": "disk"}},
    )()
    client.list_disk_images = lambda: [disk]  # type: ignore[method-assign]
    executor = SandboxToolExecutor(
        settings(),
        client_factory=lambda: client,
        connector_token_provider=lambda: "test-token",
    )

    async with executor:
        await executor.execute_tool("execute_python", {"code": "print(1)"})

    assert client.create_kwargs["disk_id"] == "disk-id-123"
    assert "disk" not in client.create_kwargs


@pytest.mark.asyncio
async def test_executor_reuses_one_sandbox_for_multiple_tools() -> None:
    sandbox = FakeSandbox(Result(stdout=json.dumps({"ok": True})))
    client = FakeClient(sandbox)
    executor = SandboxToolExecutor(
        settings(),
        client_factory=lambda: client,
        connector_token_provider=lambda: "test-token",
    )

    async with executor:
        first_id = executor.sandbox_id
        await executor.execute_tool("get_market_history", {"symbol": "MSFT"})
        await executor.execute_tool("execute_python", {"code": "print(1)"})
        assert executor.sandbox_id == first_id
        assert sandbox.deleted is False

    assert client.create_count == 1
    assert sandbox.execution_count == 2
    assert sandbox.deleted is True
    assert executor.tool_call_count == 2


@pytest.mark.asyncio
async def test_executor_requires_invocation_context() -> None:
    sandbox = FakeSandbox(Result(stdout=json.dumps({"ok": True})))
    client = FakeClient(sandbox)
    executor = SandboxToolExecutor(
        settings(),
        client_factory=lambda: client,
        connector_token_provider=lambda: "test-token",
    )

    with pytest.raises(SandboxToolError, match="async context manager"):
        await executor.execute_tool("execute_python", {"code": "print(1)"})
