from __future__ import annotations

import logging
import os
from typing import Any

from agent_framework.observability import create_resource
from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor


_credential: DefaultAzureCredential | None = None
_configured = False


def configure_agent_observability() -> None:
    global _configured, _credential
    if _configured:
        return

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        logging.info(
            "Agent observability is disabled because Application Insights is not configured."
        )
        return

    _credential = DefaultAzureCredential(
        managed_identity_client_id=os.getenv("AZURE_CLIENT_ID") or None
    )
    options: dict[str, Any] = {
        "connection_string": connection_string,
        "credential": _credential,
        "resource": create_resource(
            service_name="heavy-duty-stock-analyst",
            service_version="1.0",
        ),
        "enable_live_metrics": False,
        "enable_performance_counters": False,
        "disable_logging": True,
    }
    configure_azure_monitor(**options)
    _configured = True
    logging.info("Configured Agent Framework telemetry for Application Insights.")
