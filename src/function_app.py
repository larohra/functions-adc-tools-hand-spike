import json
import logging

import azure.functions as func
from pydantic import ValidationError

from analysis_app.agent import run_stock_analysis
from analysis_app.config import Settings
from analysis_app.models import AnalysisRequest
from analysis_app.observability import configure_agent_observability


configure_agent_observability()
app = func.FunctionApp()


@app.route(
    route="stock-analysis",
    methods=["POST"],
    auth_level=func.AuthLevel.FUNCTION,
)
async def stock_analysis_http(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be valid JSON."}, 400)

    try:
        request = AnalysisRequest.model_validate(payload)
        result = await run_stock_analysis(request, Settings.from_env())
        logging.info(
            "HTTP stock analysis completed: run_id=%s sandbox_id=%s sandbox_calls=%d "
            "email_sent=%s duration_ms=%d",
            result.run_id,
            result.sandbox_id,
            result.sandbox_tool_calls,
            result.email_sent,
            result.duration_ms,
        )
        return _json_response(result.model_dump(mode="json"), 200)
    except ValidationError as exc:
        return _json_response({"error": "Invalid request.", "details": exc.errors()}, 400)
    except Exception:
        logging.exception("HTTP stock analysis failed")
        return _json_response({"error": "Stock analysis failed. See Application Insights."}, 500)


@app.timer_trigger(
    schedule="%STOCK_ANALYSIS_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
async def stock_analysis_timer(timer: func.TimerRequest) -> None:
    settings = Settings.from_env()
    request = AnalysisRequest(
        symbols=settings.default_symbols,
        objective=settings.default_objective,
        period=settings.default_period,
        interval=settings.default_interval,
    )
    result = await run_stock_analysis(request, settings)
    logging.info(
        "Scheduled analysis completed: run_id=%s sandbox_id=%s sandbox_calls=%d "
        "email_sent=%s duration_ms=%d past_due=%s",
        result.run_id,
        result.sandbox_id,
        result.sandbox_tool_calls,
        result.email_sent,
        result.duration_ms,
        timer.past_due,
    )


def _json_response(payload: dict, status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, default=str),
        status_code=status_code,
        mimetype="application/json",
    )
