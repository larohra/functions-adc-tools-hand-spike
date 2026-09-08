from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_OUTPUT_CHARS = 60_000
PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\[\],<>=!~]+$")


def main() -> None:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    tool = payload["tool"]
    arguments = payload.get("arguments") or {}
    configuration = payload.get("configuration") or {}
    handlers = {
        "get_market_history": get_market_history,
        "get_sec_company_facts": get_sec_company_facts,
        "calculate_portfolio_metrics": calculate_portfolio_metrics,
        "execute_python": execute_python,
        "send_outlook_email": send_outlook_email,
    }
    if tool not in handlers:
        raise ValueError(f"Unsupported sandbox tool: {tool}")
    result = handlers[tool](arguments, configuration)
    print(json.dumps(result, default=str))


def ensure_packages(packages: list[str], timeout_seconds: int) -> None:
    invalid = [package for package in packages if not PACKAGE_PATTERN.fullmatch(package)]
    if invalid:
        raise ValueError(f"Only PyPI package specifications are allowed: {invalid}")
    if not packages:
        return
    marker_dir = Path("/tmp/stock-analysis-packages")
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / hashlib.sha256(
        "\n".join(sorted(packages)).encode("utf-8")
    ).hexdigest()
    if marker.exists():
        return
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--disable-pip-version-check",
        *packages,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pip install failed: {completed.stderr[-4000:]}")
    marker.touch()


def get_market_history(arguments: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    symbol = str(arguments["symbol"]).upper()
    interval = arguments.get("interval", "1d")
    records = _fetch_yahoo_chart(
        symbol,
        arguments.get("period", "1y"),
        interval,
    )
    closes = [record["close"] for record in records]
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
    ]
    return {
        "symbol": symbol,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "observations": len(records),
        "first_close": closes[0],
        "last_close": closes[-1],
        "total_return": closes[-1] / closes[0] - 1,
        "annualized_volatility": _sample_std(returns) * math.sqrt(
            _annualization_factor(interval)
        ),
        "recent_history": records[-40:],
        "source": "Yahoo Finance chart API",
    }


def get_sec_company_facts(arguments: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    ticker = str(arguments["ticker"]).upper()
    headers = {
        "User-Agent": configuration["sec_user_agent"],
        "Accept-Encoding": "gzip, deflate",
    }
    companies = _read_json_url(
        "https://www.sec.gov/files/company_tickers.json",
        headers=headers,
    )
    match = next(
        (item for item in companies.values() if item["ticker"].upper() == ticker),
        None,
    )
    if not match:
        raise RuntimeError(f"SEC CIK not found for {ticker}.")
    cik = str(match["cik_str"]).zfill(10)
    payload = _read_json_url(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        headers=headers,
    )

    wanted = {
        "Assets",
        "Liabilities",
        "StockholdersEquity",
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "OperatingIncomeLoss",
        "CashAndCashEquivalentsAtCarryingValue",
    }
    selected: dict[str, Any] = {}
    us_gaap = payload.get("facts", {}).get("us-gaap", {})
    for name in wanted:
        fact = us_gaap.get(name)
        if not fact:
            continue
        observations: list[dict[str, Any]] = []
        for unit, values in fact.get("units", {}).items():
            for value in values[-8:]:
                observations.append(
                    {
                        "unit": unit,
                        "value": value.get("val"),
                        "end": value.get("end"),
                        "form": value.get("form"),
                        "fiscal_year": value.get("fy"),
                        "fiscal_period": value.get("fp"),
                        "filed": value.get("filed"),
                    }
                )
        selected[name] = {
            "label": fact.get("label"),
            "observations": observations[-12:],
        }
    return {
        "ticker": ticker,
        "cik": cik,
        "entity_name": payload.get("entityName"),
        "facts": selected,
        "source": f"SEC EDGAR companyfacts CIK{cik}",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def calculate_portfolio_metrics(
    arguments: dict[str, Any],
    configuration: dict[str, Any],
) -> dict[str, Any]:
    symbols = [str(symbol).upper() for symbol in arguments["symbols"]]
    interval = arguments.get("interval", "1d")
    histories = {
        symbol: {
            record["timestamp"]: record["close"]
            for record in _fetch_yahoo_chart(
                symbol,
                arguments.get("period", "1y"),
                interval,
            )
        }
        for symbol in symbols
    }
    common_timestamps = sorted(
        set.intersection(*(set(history) for history in histories.values()))
    )
    if len(common_timestamps) < 3:
        raise RuntimeError("Insufficient overlapping market history.")
    prices = {
        symbol: [histories[symbol][timestamp] for timestamp in common_timestamps]
        for symbol in symbols
    }
    returns = {
        symbol: [
            values[index] / values[index - 1] - 1
            for index in range(1, len(values))
        ]
        for symbol, values in prices.items()
    }
    factor = _annualization_factor(interval)
    annual_returns = {
        symbol: (values[-1] / values[0]) ** (factor / (len(values) - 1)) - 1
        for symbol, values in prices.items()
    }
    annual_volatility = {
        symbol: _sample_std(values) * math.sqrt(factor)
        for symbol, values in returns.items()
    }
    sharpe = {
        symbol: (
            annual_returns[symbol] / annual_volatility[symbol]
            if annual_volatility[symbol]
            else None
        )
        for symbol in symbols
    }
    maximum_drawdown = {
        symbol: _maximum_drawdown(values)
        for symbol, values in prices.items()
    }
    correlations = {
        left: {
            right: _correlation(returns[left], returns[right])
            for right in symbols
        }
        for left in symbols
    }
    return {
        "symbols": symbols,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "annualized_return": annual_returns,
        "annualized_volatility": annual_volatility,
        "sharpe_ratio_zero_rate": sharpe,
        "maximum_drawdown": maximum_drawdown,
        "return_correlation": correlations,
        "observations": len(common_timestamps) - 1,
        "source": "Yahoo Finance chart API",
    }


def _fetch_yahoo_chart(symbol: str, period: str, interval: str) -> list[dict[str, Any]]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range={period}&interval={interval}&events=div%2Csplits"
    )
    payload = _read_json_url(url, headers={"User-Agent": "Mozilla/5.0"})
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"No market history returned for {symbol}.")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        close = _at(quote.get("close"), index)
        if close is None:
            continue
        rows.append(
            {
                "timestamp": int(timestamp),
                "date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
                "open": _at(quote.get("open"), index),
                "high": _at(quote.get("high"), index),
                "low": _at(quote.get("low"), index),
                "close": float(close),
                "volume": _at(quote.get("volume"), index),
            }
        )
    if len(rows) < 2:
        raise RuntimeError(f"Insufficient market history returned for {symbol}.")
    return rows


def _read_json_url(url: str, *, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read()
            content_encoding = response.headers.get("Content-Encoding", "").lower()
            if content_encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                body = gzip.decompress(body)
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:1000]}") from exc


def _at(values: list[Any] | None, index: int) -> Any:
    if values is None or index >= len(values):
        return None
    return values[index]


def _annualization_factor(interval: str) -> int:
    return {"1d": 252, "1wk": 52, "1mo": 12}.get(interval, 252)


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(
        sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    )


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return numerator / denominator if denominator else None


def _maximum_drawdown(prices: list[float]) -> float:
    peak = prices[0]
    drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        drawdown = min(drawdown, price / peak - 1)
    return drawdown


def execute_python(arguments: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    packages = [str(package) for package in arguments.get("packages") or []]
    ensure_packages(packages, _timeout(configuration))
    code = str(arguments["code"])
    with tempfile.TemporaryDirectory(prefix="stock-analysis-") as temp_dir:
        script = Path(temp_dir) / "generated.py"
        script.write_text(code, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=_timeout(configuration),
            check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    return {
        "exit_code": completed.returncode,
        "stdout": _truncate(completed.stdout),
        "stderr": _truncate(completed.stderr),
        "packages": packages,
    }


def send_outlook_email(arguments: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    recipient = str(arguments.get("to") or configuration["to_email"])
    session = _McpSession(configuration["outlook_mcp_url"])
    session.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "stock-analysis-sandbox", "version": "1.0"},
        },
        request_id=1,
    )
    session.notify("notifications/initialized", {})
    tools = session.request("tools/list", {}, request_id=2)
    available = tools.get("result", {}).get("tools", [])
    send_tool = next(
        (
            item
            for item in available
            if "sendemail" in item.get("name", "").replace("_", "").lower()
            or "send an email" in item.get("description", "").lower()
        ),
        None,
    )
    if not send_tool:
        raise RuntimeError(
            f"Outlook MCP server did not expose SendEmailV2. "
            f"Available tools: {[item.get('name') for item in available]}"
        )
    response = session.request(
        "tools/call",
        {
            "name": send_tool["name"],
            "arguments": {
                "emailMessage": {
                    "To": recipient,
                    "Subject": str(arguments["subject"]),
                    "Body": str(arguments["html_body"]),
                }
            },
        },
        request_id=3,
    )
    result = response.get("result", {})
    if result.get("isError"):
        raise RuntimeError(f"Outlook MCP tool failed: {result}")
    return {
        "sent": True,
        "recipient": recipient,
        "connector_tool": send_tool["name"],
    }


class _McpSession:
    def __init__(self, url: str) -> None:
        self.url = url
        self.session_id: str | None = None

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        request_id: int,
    ) -> dict[str, Any]:
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params}, allow_empty=True)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, payload: dict[str, Any], *, allow_empty: bool = False) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                self.session_id = response.headers.get("mcp-session-id", self.session_id)
                body = response.read().decode("utf-8")
                content_type = response.headers.get("content-type", "")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MCP request failed with HTTP {exc.code}: {body[:2000]}") from exc
        if allow_empty and not body:
            return {}
        return _parse_mcp_response(body, content_type)


def _parse_mcp_response(body: str, content_type: str) -> dict[str, Any]:
    if "text/event-stream" not in content_type:
        return json.loads(body)
    payloads = [
        line[5:].strip()
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    if not payloads:
        raise RuntimeError("MCP server returned an empty event stream.")
    return json.loads(payloads[-1])


def _timeout(configuration: dict[str, Any]) -> int:
    return int(configuration.get("timeout_seconds", 600))


def _truncate(value: str) -> str:
    if len(value) <= MAX_OUTPUT_CHARS:
        return value
    half = MAX_OUTPUT_CHARS // 2
    return value[:half] + "\n...[truncated]...\n" + value[-half:]


if __name__ == "__main__":
    main()
