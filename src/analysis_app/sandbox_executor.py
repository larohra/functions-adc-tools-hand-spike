from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlparse

from azure.containerapps.sandbox import (
    EgressHeader,
    EgressHostRule,
    EgressPolicy,
    SandboxGroupClient,
    endpoint_for_region,
)
from azure.identity import DefaultAzureCredential
from opentelemetry import context as otel_context
from opentelemetry import trace

from .config import Settings


class SandboxToolError(RuntimeError):
    pass


class SandboxToolExecutor:
    """Owns one sandbox for the complete lifetime of a Function invocation."""

    def __init__(
        self,
        settings: Settings,
        *,
        run_id: str | None = None,
        telemetry_parent: otel_context.Context | None = None,
        client_factory: Callable[[], Any] | None = None,
        connector_token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._settings = settings
        self._run_id = run_id or uuid.uuid4().hex
        self._telemetry_parent = telemetry_parent
        self._client_factory = client_factory
        self._connector_token_provider = connector_token_provider
        self._client: Any | None = None
        self._credential: DefaultAzureCredential | None = None
        self._sandbox: Any | None = None
        self._outlook_auth_configured = False
        self._execution_lock = asyncio.Lock()
        self.tool_call_count = 0
        self.sandbox_id: str | None = None
        self._runner = (
            Path(__file__).resolve().parent.parent / "sandbox_runtime" / "runner.py"
        ).read_bytes()

    async def __aenter__(self) -> "SandboxToolExecutor":
        await asyncio.to_thread(self._open_session_sync)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        try:
            await asyncio.to_thread(self._close_session_sync)
        except Exception:
            if exc_type is None:
                raise
            logging.exception(
                "Sandbox cleanup failed while propagating another invocation error"
            )
        return False

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._execution_lock:
            if self._sandbox is None:
                raise SandboxToolError(
                    "SandboxToolExecutor must be used as an async context manager."
                )
            self.tool_call_count += 1
            tracer = trace.get_tracer("stock_analysis.sandbox")
            with tracer.start_as_current_span(
                f"execute_tool {tool_name}",
                context=self._telemetry_parent,
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": tool_name,
                    "gen_ai.tool.type": "function",
                    "stock_analysis.run_id": self._run_id,
                    "stock_analysis.sandbox_id": self.sandbox_id or "",
                    "stock_analysis.tool_call_index": self.tool_call_count,
                },
            ):
                return await asyncio.to_thread(
                    self._execute_tool_sync,
                    tool_name,
                    arguments,
                )

    def _open_session_sync(self) -> None:
        if self._sandbox is not None:
            raise SandboxToolError("Sandbox session is already open.")
        try:
            self._client = self._create_client()
            disk_reference = self._resolve_disk_reference(self._client)
            self._sandbox = self._client.begin_create_sandbox(
                **disk_reference,
                cpu=self._settings.sandbox_cpu,
                memory=self._settings.sandbox_memory,
                auto_suspend_seconds=max(self._settings.sandbox_timeout_seconds + 60, 300),
                labels={
                    "app": "stock-analysis",
                    "run": self._run_id[:12],
                },
                egress_policy=self._invocation_egress_policy(),
            ).result()
            self.sandbox_id = self._sandbox.sandbox_id
            self._wait_until_ready(self._sandbox)
            self._configure_outlook_auth(self._sandbox)
            self._sandbox.write_file("/tmp/stock_tool_runner.py", self._runner)
            logging.info(
                "Created invocation sandbox: run_id=%s sandbox_id=%s",
                self._run_id,
                self.sandbox_id,
            )
        except Exception:
            try:
                self._close_session_sync()
            except Exception:
                logging.exception("Failed to clean up a partially opened sandbox session")
            raise

    def _execute_tool_sync(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        sandbox = self._sandbox
        if sandbox is None:
            raise SandboxToolError("Sandbox session is not open.")
        logging.info(
            "Executing sandbox tool: run_id=%s sandbox_id=%s tool=%s call=%d",
            self._run_id,
            self.sandbox_id,
            tool_name,
            self.tool_call_count,
        )

        input_path = f"/tmp/stock_tool_input_{uuid.uuid4().hex}.json"
        payload = {
            "tool": tool_name,
            "arguments": arguments,
            "configuration": {
                "outlook_mcp_url": self._settings.outlook_mcp_url,
                "outlook_mcp_scope": self._settings.outlook_mcp_scope,
                "to_email": self._settings.to_email,
                "sec_user_agent": self._settings.sec_user_agent,
                "timeout_seconds": self._settings.sandbox_timeout_seconds,
            },
        }
        sandbox.write_file(input_path, json.dumps(payload).encode("utf-8"))
        execution = sandbox.exec(
            "timeout "
            f"{self._settings.sandbox_timeout_seconds}s "
            f"python3 /tmp/stock_tool_runner.py {input_path}; "
            f"rc=$?; rm -f {input_path}; exit $rc"
        )
        if execution.exit_code != 0:
            raise SandboxToolError(
                f"Sandbox tool {tool_name!r} failed with exit code "
                f"{execution.exit_code}: {(execution.stderr or execution.stdout or '')[-2000:]}"
            )
        try:
            result = json.loads((execution.stdout or "").strip())
        except json.JSONDecodeError as exc:
            raise SandboxToolError(
                f"Sandbox tool {tool_name!r} returned invalid JSON: "
                f"{(execution.stdout or '')[-2000:]}"
            ) from exc
        if not isinstance(result, dict):
            raise SandboxToolError(f"Sandbox tool {tool_name!r} returned a non-object result.")
        return result

    def _close_session_sync(self) -> None:
        cleanup_error: Exception | None = None
        sandbox = self._sandbox
        client = self._client
        credential = self._credential
        self._sandbox = None
        self._client = None
        self._credential = None
        self._outlook_auth_configured = False

        if sandbox is not None:
            try:
                sandbox.delete()
                logging.info(
                    "Deleted invocation sandbox: run_id=%s sandbox_id=%s tool_calls=%d",
                    self._run_id,
                    self.sandbox_id,
                    self.tool_call_count,
                )
            except Exception as exc:
                cleanup_error = exc
                logging.exception("Failed to delete sandbox %s", self.sandbox_id)
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
                logging.exception("Failed to close SandboxGroupClient")
        if credential is not None:
            try:
                credential.close()
            except Exception as exc:
                cleanup_error = cleanup_error or exc
                logging.exception("Failed to close sandbox credential")
        if cleanup_error is not None:
            raise SandboxToolError(f"Sandbox cleanup failed: {cleanup_error}") from cleanup_error

    def _resolve_disk_reference(self, client: Any) -> dict[str, str]:
        for disk in client.list_disk_images():
            labels = getattr(disk, "labels", {}) or {}
            if getattr(disk, "name", None) == self._settings.sandbox_disk:
                return {"disk_id": disk.id}
            if labels.get("name") == self._settings.sandbox_disk:
                return {"disk_id": disk.id}
        return {"disk": self._settings.sandbox_disk}

    def _create_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        self._credential = DefaultAzureCredential(
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None
        )
        return SandboxGroupClient(
            endpoint_for_region(self._settings.sandbox_region),
            self._credential,
            subscription_id=self._settings.sandbox_subscription_id,
            resource_group=self._settings.sandbox_resource_group,
            sandbox_group=self._settings.sandbox_group,
        )

    def _invocation_egress_policy(self) -> EgressPolicy:
        connector_host = urlparse(self._settings.outlook_mcp_url).hostname
        hosts = [
            host
            for host in self._settings.sandbox_egress_hosts
            if host != connector_host
        ]
        return EgressPolicy(
            default_action="Deny",
            traffic_inspection="Full",
            host_rules=[EgressHostRule(pattern=host, action="Allow") for host in hosts],
        )

    def _configure_outlook_auth(self, sandbox: Any) -> None:
        if self._outlook_auth_configured:
            return
        connector_host = urlparse(self._settings.outlook_mcp_url).hostname
        if not connector_host:
            raise SandboxToolError("O365_MCP_SERVER_URL does not contain a valid host.")
        token = self._get_connector_token()

        sandbox.add_egress_transform_rule(
            host=connector_host,
            headers=[
                EgressHeader(
                    operation="Set",
                    name="Authorization",
                    value=f"Bearer {token}",
                )
            ],
            name="outlook-mcp-auth",
        )
        self._outlook_auth_configured = True

    def _get_connector_token(self) -> str:
        if self._connector_token_provider is not None:
            return self._connector_token_provider()
        credential = DefaultAzureCredential(
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None
        )
        try:
            return credential.get_token(self._settings.outlook_mcp_scope).token
        finally:
            credential.close()

    @staticmethod
    def _wait_until_ready(sandbox: Any, timeout_seconds: int = 45) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = sandbox.exec("true")
            if result.exit_code == 0:
                return
            time.sleep(1.5)
        raise SandboxToolError("Sandbox exec endpoint did not become ready.")
