from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AnalysisRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=10)
    objective: str = Field(min_length=10, max_length=4000)
    period: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y"] = "1y"
    interval: Literal["1d", "1wk", "1mo"] = "1d"
    additional_context: str | None = Field(default=None, max_length=8000)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, symbols: list[str]) -> list[str]:
        normalized = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        if not normalized:
            raise ValueError("At least one stock symbol is required.")
        if any(not symbol.replace(".", "").replace("-", "").isalnum() for symbol in normalized):
            raise ValueError("Symbols may contain only letters, numbers, dots, and hyphens.")
        return list(dict.fromkeys(normalized))


class AnalysisResult(BaseModel):
    run_id: str
    sandbox_id: str
    report_html: str
    email_sent: bool
    sandbox_tool_calls: int
    duration_ms: int
