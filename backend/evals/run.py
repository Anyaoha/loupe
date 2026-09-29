"""Eval harness for the insight prompt. Run before changing the prompt or swapping the model.

    python -m evals.run                      # mock provider, deterministic, no network
    python -m evals.run --provider anthropic # live model (needs LOUPE_ANTHROPIC_API_KEY)
    python -m evals.run --provider bedrock

Two families of checks:
  1. Quality gates on golden cases  - schema, evidence verification rate, confidence band,
     no invented people, right top signal, brevity, honesty about partial coverage.
  2. Guardrail regressions           - feed a deliberately misbehaving mock model and assert
     the verifier catches each failure mode. If these ever pass "too well" the guardrail
     is broken, not the model.

Exit code is non-zero on any failure so this can sit in CI.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from loupe import db as dbmod
from loupe.config import Settings
from loupe.insights import synthesize
from loupe.llm import build_provider
from loupe.llm.mock_provider import MockProvider
from loupe.schemas import InsightOut, SignalsReport

CASES = Path(__file__).parent / "cases"
MAX_HEADLINE = 90
MAX_NARRATIVE = 900
MIN_VERIFIED_RATIO = 0.9


@dataclass
class Result:
    name: str
    passed: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    confidence: float | None = None


def load_cases() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(CASES.glob("*.json"))]


def quality_checks(insight: InsightOut, exp: dict) -> list[tuple[str, bool, str]]:
    v = insight.verification
    ratio = (v.claims_verified / v.claims_total) if v.claims_total else 0.0
    lo, hi = exp["confidence"]
    checks = [
        ("has evidence", v.claims_total >= 2, f"{v.claims_total} claims"),
        ("evidence verified", ratio >= MIN_VERIFIED_RATIO, f"{ratio:.0%} verified"),
        ("no invented people", not v.unknown_actors, ", ".join(v.unknown_actors) or "ok"),
        ("confidence in band", lo <= insight.confidence <= hi, f"{insight.confidence} in [{lo}, {hi}]"),
        ("headline brevity", len(insight.headline) <= MAX_HEADLINE, f"{len(insight.headline)} chars"),
        ("narrative brevity", len(insight.narrative) <= MAX_NARRATIVE, f"{len(insight.narrative)} chars"),
    ]
    if exp.get("expect_top_signal"):
        checks.append(("top signal used", exp["expect_top_signal"] in insight.signals_considered, ", ".join(insight.signals_considered) or "none"))
    if not exp.get("root_cause_allowed", True):
        checks.append(("no root cause on quiet data", insight.root_cause is None, "root_cause present" if insight.root_cause else "ok"))
    if exp.get("must_mention_coverage"):
        text = f"{insight.headline} {insight.narrative}".lower()
        mentions = any(w in text for w in ("coverage", "partial", "incomplete", "not fully", "only covers", "synced"))
        checks.append(("acknowledges partial coverage", mentions and insight.confidence <= insight.confidence_breakdown.coverage_cap, f"cap={insight.confidence_breakdown.coverage_cap}"))
    return checks


async def run_quality(provider, cases: list[dict], max_tokens: int) -> list[Result]:
    out = []
    for case in cases:
        report = SignalsReport.model_validate(case["report"])
        try:
            insight = await synthesize(report, provider, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001
            out.append(Result(case["name"], False, [("synthesis", False, f"{type(exc).__name__}: {str(exc)[:120]}")]))
            continue
        checks = quality_checks(insight, case["expectations"])
        out.append(Result(case["name"], all(ok for _, ok, _ in checks), checks, insight.confidence))
    return out


async def run_guardrails(cases: list[dict], max_tokens: int) -> list[Result]:
    drifting = next(c for c in cases if c["name"] == "drifting_repo")
    report = SignalsReport.model_validate(drifting["report"])
    honest = await synthesize(report, MockProvider(), max_tokens=max_tokens)
    scenarios = [
        ("guardrail: hallucinated numbers", MockProvider(hallucinate_values=True), lambda i: i.verification.claims_failed >= 2 and i.confidence < honest.confidence),
        ("guardrail: invented fact id", MockProvider(invent_facts=True), lambda i: any(e.note == "fact id does not exist" for e in i.evidence) and i.confidence < honest.confidence),
        ("guardrail: unknown person", MockProvider(unknown_actor="mallory"), lambda i: i.verification.unknown_actors == ["mallory"] and i.confidence < honest.confidence),
        ("guardrail: ungrounded action", MockProvider(ungrounded_action=True), lambda i: any(not a.grounded for a in i.recommended_actions) and i.confidence < honest.confidence),
    ]
    out = []
    for name, provider, predicate in scenarios:
        insight = await synthesize(report, provider, max_tokens=max_tokens)
        ok = predicate(insight)
        out.append(Result(name, ok, [("verifier caught it", ok, f"confidence {honest.confidence} -> {insight.confidence}, failed={insight.verification.claims_failed}, unknown={insight.verification.unknown_actors}")], insight.confidence))
    return out


def print_results(title: str, results: list[Result]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.name}" + (f"  (confidence {r.confidence})" if r.confidence is not None else ""))
        for name, ok, detail in r.checks:
            print(f"      {'ok ' if ok else 'XX '} {name:<32} {detail}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["mock", "anthropic", "bedrock"], default="mock")
    ap.add_argument("--db", default="sqlite:///./evals/results/traces.db", help="where to record LLM traces for this run")
    ap.add_argument("--skip-guardrails", action="store_true")
    args = ap.parse_args()

    Path("evals/results").mkdir(parents=True, exist_ok=True)
    dbmod.init_engine(args.db)
    settings = Settings(llm_provider=args.provider)
    provider = build_provider(settings)
    cases = load_cases()
    if not cases:
        print("no cases found; run: python -m evals.make_cases", file=sys.stderr)
        return 2

    quality = await run_quality(provider, cases, settings.llm_max_tokens)
    print_results(f"Quality gates  [provider={provider.system} model={provider.model}]", quality)
    guardrails = [] if args.skip_guardrails else await run_guardrails(cases, settings.llm_max_tokens)
    if guardrails:
        print_results("Guardrail regressions  [adversarial mock]", guardrails)

    total = quality + guardrails
    failed = [r for r in total if not r.passed]
    print(f"\n{len(total) - len(failed)}/{len(total)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
