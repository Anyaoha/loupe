"""Per-call tracing using OpenTelemetry GenAI semantic convention attribute names.

Stored in the local DB so the /llm/traces endpoint works with zero infrastructure. Swapping
the sink for an OTLP exporter is a one-class change because the attribute names already
match the convention (gen_ai.system, gen_ai.request.model, gen_ai.usage.input_tokens, ...).
"""

import logging
import time
import uuid
from dataclasses import dataclass

from loupe.db import session_scope
from loupe.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse
from loupe.logging_setup import redact
from loupe.models import LlmTrace

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Indicative; used for cost attribution, not billing.
PRICE_PER_MTOK = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-1": (15.0, 75.0),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    for key, (pin, pout) in PRICE_PER_MTOK.items():
        if key in model:
            return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 6)
    return 0.0


@dataclass
class Traced:
    response: LLMResponse
    trace_id: str
    latency_ms: float


async def traced_complete(provider: LLMProvider, req: LLMRequest, *, purpose: str, prompt_version: str) -> Traced:
    trace_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    try:
        resp = await provider.complete(req)
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000
        _record(trace_id, purpose, prompt_version, provider, None, latency, status="error", error_type=type(exc).__name__, error_message=redact(str(exc))[:500])
        raise LLMError(redact(str(exc))) from exc
    latency = (time.perf_counter() - t0) * 1000
    _record(trace_id, purpose, prompt_version, provider, resp, latency, status="ok")
    return Traced(response=resp, trace_id=trace_id, latency_ms=latency)


def _record(trace_id, purpose, prompt_version, provider, resp, latency_ms, *, status, error_type=None, error_message=None):
    row = LlmTrace(
        trace_id=trace_id,
        purpose=purpose,
        prompt_version=prompt_version,
        gen_ai_system=provider.system,
        gen_ai_request_model=provider.model,
        gen_ai_response_model=resp.model if resp else None,
        gen_ai_usage_input_tokens=resp.input_tokens if resp else 0,
        gen_ai_usage_output_tokens=resp.output_tokens if resp else 0,
        gen_ai_response_finish_reason=resp.finish_reason if resp else None,
        latency_ms=round(latency_ms, 2),
        estimated_cost_usd=estimate_cost_usd(provider.model, resp.input_tokens, resp.output_tokens) if resp else 0.0,
        status=status,
        error_type=error_type,
        error_message=error_message,
    )
    with session_scope() as s:
        s.add(row)
    log.info(
        "llm call purpose=%s system=%s model=%s status=%s latency_ms=%.0f in=%s out=%s",
        purpose, provider.system, provider.model, status, latency_ms, row.gen_ai_usage_input_tokens, row.gen_ai_usage_output_tokens,
    )
