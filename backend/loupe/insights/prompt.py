"""The insight prompt. Versioned so cached insights and traces can be tied to the exact
wording that produced them. Bump PROMPT_VERSION on any change to SYSTEM or the user
template; the eval harness is the gate for doing so.
"""

import json

from loupe.schemas import SignalsReport

PROMPT_VERSION = "insight-v1.2"

SYSTEM = """You are an engineering analyst writing a short brief for an engineering manager.

You will receive a JSON document with:
  - "facts": a table of numeric facts keyed by fact id. These are the ONLY numbers that exist.
  - "signals": pre-computed anomalies, each with evidence_refs pointing into the facts table.
  - "coverage": how much of the requested window the data actually covers.

Rules:
  1. Every number you mention must come from the facts table and be cited by its fact id in the evidence list.
  2. Do not invent people. Only name a person if their login appears in a fact id or fact value.
  3. If the signals support a root cause, state one hypothesis and how confident you are in it. If they do not, set root_cause to null. Do not speculate beyond the data.
  4. Prefer the strongest signal. Two or three sentences of narrative. No bullet points, no headers, no preamble.
  5. If coverage.covered_ratio is below 1.0, say so briefly and lower your confidence.
  6. Respond with a single JSON object and nothing else, matching exactly:

{
  "headline": "one line, under 90 characters",
  "narrative": "2-4 sentences",
  "root_cause": {"hypothesis": "one sentence", "model_confidence": 0.0-1.0} | null,
  "confidence": 0.0-1.0,
  "evidence": [{"fact_id": "id from facts", "claim": "short sentence", "quoted_value": number or string}],
  "signals_considered": ["signal ids you used"]
}"""

USER_TEMPLATE = """Repository: {repo}
Window: {start} to {end} ({days} days). Baseline for comparisons: the preceding {days} days.

<data>
{data}
</data>

Write the brief."""


def build_user_prompt(report: SignalsReport) -> str:
    data = {
        "coverage": report.coverage.model_dump(mode="json"),
        "signals": [s.model_dump(mode="json") for s in report.signals],
        "facts": {fid: f.model_dump(mode="json") for fid, f in report.facts.items()},
    }
    return USER_TEMPLATE.format(
        repo=f"{report.repository.owner}/{report.repository.name}",
        start=report.window.start.date().isoformat(),
        end=report.window.end.date().isoformat(),
        days=int(report.window.days),
        data=json.dumps(data, separators=(",", ":"), default=str),
    )
