"""Turns a SignalsReport into a verified InsightOut via one LLM call.

Flow: build prompt -> traced model call -> parse strict JSON -> verify claims -> compute
confidence. A parse failure gets exactly one retry with the parser error appended; a second
failure surfaces as a 502 rather than a made-up insight.
"""

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from loupe.insights.prompt import PROMPT_VERSION, SYSTEM, build_user_prompt
from loupe.insights.verifier import verify
from loupe.llm.base import LLMError, LLMProvider, LLMRequest
from loupe.llm.tracing import traced_complete
from loupe.schemas import InsightOut, RootCause, SignalsReport
from loupe.timeutil import utcnow

log = logging.getLogger(__name__)


class _RawRootCause(BaseModel):
    hypothesis: str = Field(max_length=500)
    model_confidence: float = Field(ge=0, le=1)


class _RawEvidence(BaseModel):
    fact_id: str = Field(max_length=200)
    claim: str = Field(max_length=300)
    quoted_value: float | int | str | None = None


class _RawInsight(BaseModel):
    headline: str = Field(max_length=160)
    narrative: str = Field(max_length=2000)
    root_cause: _RawRootCause | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[_RawEvidence] = Field(max_length=12)
    signals_considered: list[str] = Field(default_factory=list, max_length=20)


class InsightParseError(LLMError):
    pass


async def synthesize(report: SignalsReport, provider: LLMProvider, *, max_tokens: int) -> InsightOut:
    user = build_user_prompt(report)
    req = LLMRequest(system=SYSTEM, user=user, max_tokens=max_tokens)

    traced = await traced_complete(provider, req, purpose="insight", prompt_version=PROMPT_VERSION)
    try:
        raw = _parse(traced.response.text)
    except InsightParseError as first:
        log.warning("insight parse failed (%s); retrying once", first)
        retry = LLMRequest(system=SYSTEM, user=f"{user}\n\nYour previous reply was not valid JSON for the schema ({first}). Reply with only the JSON object.", max_tokens=max_tokens)
        traced = await traced_complete(provider, retry, purpose="insight-retry", prompt_version=PROMPT_VERSION)
        raw = _parse(traced.response.text)

    known_signal_ids = {s.id for s in report.signals}
    verified = verify(
        raw_evidence=[e.model_dump() for e in raw.evidence],
        narrative_text=f"{raw.headline}\n{raw.narrative}\n{raw.root_cause.hypothesis if raw.root_cause else ''}",
        model_confidence=raw.confidence,
        facts=report.facts,
        covered_ratio=report.coverage.covered_ratio,
    )
    return InsightOut(
        repository=report.repository,
        window=report.window,
        headline=raw.headline,
        narrative=raw.narrative,
        root_cause=RootCause(hypothesis=raw.root_cause.hypothesis, model_confidence=raw.root_cause.model_confidence) if raw.root_cause else None,
        confidence=verified.confidence.final,
        confidence_breakdown=verified.confidence,
        evidence=verified.evidence,
        signals_considered=[s for s in raw.signals_considered if s in known_signal_ids],
        verification=verified.verification,
        prompt_version=PROMPT_VERSION,
        model=traced.response.model,
        trace_id=traced.trace_id,
        cached=False,
        generated_at=utcnow(),
    )


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _parse(text: str) -> _RawInsight:
    m = _JSON_BLOCK.search(text or "")
    if not m:
        raise InsightParseError("no JSON object in response")
    try:
        return _RawInsight.model_validate(json.loads(m.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise InsightParseError(str(exc)[:300]) from exc
