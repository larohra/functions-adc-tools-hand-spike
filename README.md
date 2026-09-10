# Sandboxed stock-analysis Function App

This spike runs a Microsoft Agent Framework (MAF) equity-research agent in a Python Azure
Functions worker while forcing every model-requested tool call into Azure Container Apps Sandbox.
The worker contains tool schemas only. Each HTTP or timer invocation creates one isolated sandbox
in the sanctioned Sandbox Group. `SandboxFunctionMiddleware` routes every tool call to that same
sandbox and persistent filesystem. The sandbox is deleted when the Function invocation completes.

The app exposes:

- `POST /stock-analysis` for synchronous HTTP analysis.
- A weekday timer trigger, configurable with `STOCK_ANALYSIS_SCHEDULE`.
- Automatic Outlook email delivery through a Connector Namespace MCP server. Email execution
  occurs in the same invocation-scoped sandbox.

> The synchronous HTTP path can exceed Azure's front-end response limit for long analyses. The
> timer path is the reliable option for heavy runs; production HTTP workloads should normally use
> a queue/deferred response pattern.

## Architecture

### At-a-glance block diagram

```mermaid
flowchart LR
    User["User or<br/>Timer Trigger"]

    subgraph Functions["Azure Functions App - orchestration"]
        Host["Functions Host"]
        Worker["Python Worker"]
        MAF["Microsoft Agent Framework<br/>Agent Run"]
        Middleware["SandboxFunctionMiddleware"]

        Host --> Worker
        Worker --> MAF
        MAF --> Middleware
    end

    subgraph Model["Governed model access"]
        APIM["APIM AI Gateway"]
        LLM["Azure OpenAI<br/>gpt-5.6-luna"]

        APIM <--> LLM
    end

    subgraph Execution["Isolated execution - one sandbox per invocation"]
        Sandbox["ACA Sandbox<br/>Tools and generated Python"]
    end

    Sources["Allow-listed data sources<br/>Market data, SEC, PyPI"]
    Outlook["Outlook Connector MCP"]
    Result["HTML report and<br/>run metrics"]

    User --> Host
    MAF <--> APIM
    Middleware <--> Sandbox
    Sandbox <--> Sources
    Sandbox --> Outlook
    MAF --> Result
    Outlook --> Result
    Result --> User
    Worker -. "Delete after run" .-> Sandbox

    classDef trigger fill:#e8f3ff,stroke:#1877c9,color:#10253f;
    classDef orchestration fill:#eaf7ee,stroke:#268447,color:#102d1b;
    classDef gateway fill:#fff4dc,stroke:#c47a00,color:#422b00;
    classDef isolated fill:#f3eaff,stroke:#7446a8,color:#29163e;
    classDef external fill:#f4f5f7,stroke:#687078,color:#202428;
    classDef outcome fill:#e8f8f8,stroke:#168787,color:#123535;

    class User trigger;
    class Host,Worker,MAF,Middleware orchestration;
    class APIM,LLM gateway;
    class Sandbox isolated;
    class Sources,Outlook external;
    class Result outcome;
```

The green Functions area owns orchestration and the agent loop. The purple sandbox owns all tool
and generated-code execution. A single sandbox is reused for the invocation and deleted after the
report and email result are produced.

### Detailed request sequence

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Scheduler
    participant Host as Azure Functions Host
    participant Worker as Python Functions Worker
    participant Sandbox as ACA Sandbox
    participant MAF as Microsoft Agent Framework
    participant APIM as APIM AI Gateway
    participant LLM as Azure OpenAI Model
    participant Data as Approved Data Sources
    participant Outlook as Outlook Connector MCP

    User->>Host: POST /stock-analysis or timer fires
    Host->>Worker: Invoke Function with validated trigger data
    Worker->>Worker: Validate request and create run ID
    Worker->>Sandbox: Create one invocation-scoped sandbox
    Worker->>Sandbox: Upload sandbox tool runner
    Worker->>MAF: agent.run(analysis prompt)

    loop Each reasoning turn
        MAF->>APIM: Chat completion + run correlation ID
        APIM->>LLM: Governed model request
        LLM-->>APIM: Assistant response or tool calls
        APIM-->>MAF: Model response

        opt Model requests one or more tools
            MAF->>Worker: Invoke declared tool schema
            Worker->>Worker: SandboxFunctionMiddleware intercepts call
            Worker->>Sandbox: Execute tool with arguments
            Sandbox->>Data: Allow-listed market, SEC, or PyPI request
            Data-->>Sandbox: Data or package response
            Sandbox-->>Worker: Structured tool result
            Worker-->>MAF: Return result to the agent turn
        end
    end

    MAF-->>Worker: Final HTML research report
    Worker->>Sandbox: Execute send_outlook_email
    Sandbox->>Outlook: Send through allow-listed MCP operation
    Outlook-->>Sandbox: Delivery result
    Sandbox-->>Worker: email_sent status
    Worker->>Sandbox: Delete invocation sandbox
    Worker-->>Host: AnalysisResult + run metrics
    Host-->>User: HTTP response or timer completion log
```

**Execution boundary:** MAF and the LLM orchestration run in the Python worker, but declared tool
bodies never execute there. `SandboxFunctionMiddleware` intercepts each tool request and sends it
to the single ACA Sandbox owned by that Function invocation. The sandbox is reused for the full
agent run and deleted during cleanup.

1. The Function App calls `gpt-5.6-luna` through the configured APIM AI Gateway.
2. MAF exposes market-history, SEC-facts, portfolio-metrics, and generated-Python schemas.
3. `SandboxFunctionMiddleware` never calls the Python tool body.
4. Each Function invocation boots one sandbox from the `stock-analysis-python` disk and reuses it
   for all tool calls and generated code in that run.
5. The sandbox applies deny-by-default egress and allows only PyPI, selected market-data hosts,
   SEC hosts, Entra login, and the deployed Outlook MCP endpoint.
6. The completed HTML report is sent from the same invocation-scoped sandbox and then returned to
   the HTTP caller or logged by the timer invocation.

## Request

```json
{
  "symbols": ["MSFT", "NVDA", "AAPL"],
  "objective": "Compare quality, growth, valuation, momentum, and downside risk.",
  "period": "1y",
  "interval": "1d",
  "additional_context": "Focus on a 12-24 month research horizon."
}
```

## Configure and deploy

Install Azure Developer CLI before deployment. Replace each placeholder below with values from
your Azure environment. Do not commit subscription keys, connector credentials, recipient
addresses, or generated `azd` environment files.

```powershell
azd auth login
azd init
azd env set AZURE_LOCATION westus2
azd env set ACA_SANDBOX_SUBSCRIPTION_ID <subscription-id>
azd env set ACA_SANDBOX_RESOURCE_GROUP <sandbox-resource-group>
azd env set ACA_SANDBOX_GROUP <sanctioned-sandbox-group>
azd env set ACA_SANDBOX_REGION westus2
azd env set ACA_SANDBOX_DISK stock-analysis-python
azd env set ACA_SANDBOX_BASE_IMAGE mcr.microsoft.com/devcontainers/python:1-3.13-bookworm
azd env set APIM_AI_GATEWAY_ENDPOINT https://<apim-name>.azure-api.net/<model-api-path>
azd env set APIM_AI_MODEL gpt-5.6-luna
azd env set TO_EMAIL <recipient@example.com>
azd up
```

Create the custom group disk after provisioning. Export the `azd` values into the current shell,
then run:

```powershell
$values = azd env get-values
$values | ForEach-Object {
  if ($_ -match '^([^=]+)="(.*)"$') {
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
  }
}
.venv\Scripts\python.exe scripts\prepare_sandbox_disk.py
```

Authorize the Office 365 connection at:

```text
https://connectors.azure.com/<subscription-id>/rg-<azd-environment>/<connector-gateway-name>/overview
```

The connector must show `Connected` before the first email smoke test.

## Local validation

```powershell
.venv\Scripts\python.exe -m pip install -r src\requirements.txt pytest pytest-asyncio
.venv\Scripts\python.exe -m pytest -q
```

For local Functions execution, copy deployed `O365_MCP_SERVER_URL` into
`src\local.settings.json`, start Azurite, and run `func start` from `src`.

## Security boundaries

- Tool bodies fail if middleware is missing, preventing accidental worker execution.
- Sandboxes use deny-by-default egress with full inspection.
- Generated code and PyPI packages execute only in the sandbox.
- The Function identity receives Sandbox Group data-plane and connector access. Connector tokens
  are injected by the sandbox egress proxy and never staged inside the sandbox filesystem.
- No model, APIM, or connector credentials are committed. `APIM_SUBSCRIPTION_KEY` is optional and
  should be stored only in `azd`/Function App settings if APIM requires it.
- The app deletes each sandbox after success, failure, invalid output, or timeout and surfaces
  cleanup failures.
