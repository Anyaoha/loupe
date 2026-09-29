"""Checks a model's insight against the facts it was given, then adjusts confidence.

This is the hallucination guardrail. The model is asked to cite fact ids; we check that
each cited id exists, that the value it quoted matches the real one (within a tolerance
for rounding), and that every person it names is someone who actually appears in the data.

Confidence is then computed, not copied:
  after_verification = model_confidence * (1 - penalty)
  coverage_cap       = 0.5 + 0.5 * covered_ratio        # partial data can never yield high confidence
  final              = min(after_verification, coverage_cap, evidence_cap)
where evidence_cap = 0.5 when fewer than two claims verified (a story with no checkable
support is at best a coin flip).
"""

import re
from dataclasses import dataclass

from loupe.schemas import ConfidenceBreakdown, EvidenceClaim, Fact, Verification

PENALTY_PER_FAILED_CLAIM = 0.15
PENALTY_PER_UNKNOWN_ACTOR = 0.20
MAX_PENALTY = 0.8
MIN_VERIFIED_FOR_HIGH_CONFIDENCE = 2
LOW_EVIDENCE_CAP = 0.5
RELATIVE_TOLERANCE = 0.02
ABSOLUTE_TOLERANCE = 0.5

@dataclass
class VerifiedInsight:
    evidence: list[EvidenceClaim]
    verification: Verification
    confidence: ConfidenceBreakdown


def verify(
    *,
    raw_evidence: list[dict],
    narrative_text: str,
    model_confidence: float,
    facts: dict[str, Fact],
    covered_ratio: float,
) -> VerifiedInsight:
    evidence = [_check_claim(e, facts) for e in raw_evidence]
    verified = sum(1 for e in evidence if e.verified)
    failed = len(evidence) - verified

    known_actors = _actors_in_facts(facts)
    unknown = _unknown_actors(narrative_text, known_actors)

    penalty = min(MAX_PENALTY, failed * PENALTY_PER_FAILED_CLAIM + len(unknown) * PENALTY_PER_UNKNOWN_ACTOR)
    after_verification = model_confidence * (1 - penalty)
    coverage_cap = 0.5 + 0.5 * max(0.0, min(1.0, covered_ratio))
    evidence_cap = LOW_EVIDENCE_CAP if verified < MIN_VERIFIED_FOR_HIGH_CONFIDENCE else 1.0
    final = min(after_verification, coverage_cap, evidence_cap)

    return VerifiedInsight(
        evidence=evidence,
        verification=Verification(claims_total=len(evidence), claims_verified=verified, claims_failed=failed, unknown_actors=unknown, penalty=round(penalty, 3)),
        confidence=ConfidenceBreakdown(
            model_confidence=round(model_confidence, 3),
            after_verification=round(after_verification, 3),
            coverage_cap=round(coverage_cap, 3),
            final=round(max(0.0, final), 3),
        ),
    )


def _check_claim(raw: dict, facts: dict[str, Fact]) -> EvidenceClaim:
    fid = str(raw.get("fact_id", ""))
    claim = str(raw.get("claim", ""))[:300]
    quoted = raw.get("quoted_value")
    fact = facts.get(fid)
    if fact is None:
        return EvidenceClaim(fact_id=fid, claim=claim, quoted_value=quoted, verified=False, actual_value=None, note="fact id does not exist")
    if quoted is None:
        return EvidenceClaim(fact_id=fid, claim=claim, quoted_value=None, verified=True, actual_value=fact.value, note="no value quoted; id exists")
    ok = _values_match(quoted, fact.value)
    return EvidenceClaim(fact_id=fid, claim=claim, quoted_value=quoted, verified=ok, actual_value=fact.value, note=None if ok else "quoted value does not match")


def _values_match(quoted, actual) -> bool:
    if actual is None:
        return False
    if isinstance(actual, str) or isinstance(quoted, str):
        q, a = str(quoted).strip().lower(), str(actual).strip().lower()
        if q == a:
            return True
        try:
            quoted, actual = float(q.rstrip("%")), float(a)
        except ValueError:
            return False
    q, a = float(quoted), float(actual)
    if abs(q - a) <= ABSOLUTE_TOLERANCE:
        return True
    # Ratios are often quoted as percentages: 0.57 -> 57
    if 0 <= a <= 1 and abs(q / 100 - a) <= RELATIVE_TOLERANCE:
        return True
    return abs(q - a) <= RELATIVE_TOLERANCE * max(abs(a), 1e-9)


def _actors_in_facts(facts: dict[str, Fact]) -> set[str]:
    actors: set[str] = set()
    for fid, f in facts.items():
        parts = fid.split(".")
        if len(parts) >= 4 and parts[1] in ("reviewer", "committer", "pr_author"):
            actors.add(parts[2].lower())
        if isinstance(f.value, str):
            actors.update(a.strip().lower() for a in f.value.split(",") if a.strip())
    return actors


def _unknown_actors(text: str, known: set[str]) -> list[str]:
    """Flag @handles the model used that never appear in the data. We only look at
    explicit @mentions: scanning prose for bare words would flag ordinary English."""
    mentioned = {m.group(1) for m in re.finditer(r"@([A-Za-z0-9][A-Za-z0-9-]{1,38})", text)}
    return sorted(m for m in mentioned if m.lower() not in known)
