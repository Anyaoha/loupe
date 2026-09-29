"""The verifier is the hallucination guardrail, so it gets the most adversarial tests."""

from loupe.insights.verifier import verify
from loupe.schemas import Fact

FACTS = {
    "cur.totals.prs_merged": Fact(label="PRs merged", value=42),
    "cur.flow.time_to_merge_median": Fact(label="median ttm", value=31.25, unit="hours"),
    "cur.concentration.review_top1_share": Fact(label="top reviewer share", value=0.5714, unit="ratio"),
    "cur.reviewer.bob.reviews": Fact(label="reviews by bob", value=12),
    "cur.committers.absent_vs_baseline": Fact(label="absent", value="carol, dave"),
}


def run(evidence, text="Steady period.", conf=0.8, coverage=1.0):
    return verify(raw_evidence=evidence, narrative_text=text, model_confidence=conf, facts=FACTS, covered_ratio=coverage)


def test_exact_and_rounded_values_verify():
    v = run([
        {"fact_id": "cur.totals.prs_merged", "claim": "42 merged", "quoted_value": 42},
        {"fact_id": "cur.flow.time_to_merge_median", "claim": "about 31 hours", "quoted_value": 31},
        {"fact_id": "cur.concentration.review_top1_share", "claim": "57% of reviews", "quoted_value": 57},
        {"fact_id": "cur.concentration.review_top1_share", "claim": "0.57 share", "quoted_value": "57%"},
    ])
    assert v.verification.claims_failed == 0
    assert v.confidence.final == 0.8


def test_wrong_number_is_flagged_and_penalised():
    v = run([
        {"fact_id": "cur.totals.prs_merged", "claim": "60 merged", "quoted_value": 60},
        {"fact_id": "cur.reviewer.bob.reviews", "claim": "bob did 12", "quoted_value": 12},
        {"fact_id": "cur.flow.time_to_merge_median", "claim": "31h", "quoted_value": 31.3},
    ])
    failed = [e for e in v.evidence if not e.verified]
    assert [e.fact_id for e in failed] == ["cur.totals.prs_merged"]
    assert failed[0].actual_value == 42
    assert v.verification.penalty == 0.15
    assert v.confidence.final == round(0.8 * 0.85, 3)


def test_invented_fact_id_is_flagged():
    v = run([
        {"fact_id": "cur.totals.deploys", "claim": "deploys 9", "quoted_value": 9},
        {"fact_id": "cur.totals.prs_merged", "claim": "42", "quoted_value": 42},
        {"fact_id": "cur.reviewer.bob.reviews", "claim": "12", "quoted_value": 12},
    ])
    bad = next(e for e in v.evidence if e.fact_id == "cur.totals.deploys")
    assert bad.verified is False and bad.note == "fact id does not exist"


def test_unknown_actor_in_narrative_penalised_known_actor_not():
    ok = run([{"fact_id": "cur.reviewer.bob.reviews", "quoted_value": 12}, {"fact_id": "cur.totals.prs_merged", "quoted_value": 42}], text="@bob carried reviews while @carol was out.")
    assert ok.verification.unknown_actors == []
    bad = run([{"fact_id": "cur.reviewer.bob.reviews", "quoted_value": 12}, {"fact_id": "cur.totals.prs_merged", "quoted_value": 42}], text="@mallory did most of it.")
    assert bad.verification.unknown_actors == ["mallory"]
    assert bad.confidence.final == round(0.8 * 0.8, 3)


def test_partial_coverage_caps_confidence():
    v = run([{"fact_id": "cur.totals.prs_merged", "quoted_value": 42}, {"fact_id": "cur.reviewer.bob.reviews", "quoted_value": 12}], conf=0.95, coverage=0.4)
    assert v.confidence.coverage_cap == 0.7
    assert v.confidence.final == 0.7


def test_too_little_verified_evidence_caps_at_half():
    v = run([{"fact_id": "cur.totals.prs_merged", "quoted_value": 42}], conf=0.9)
    assert v.verification.claims_verified == 1
    assert v.confidence.final == 0.5


def test_penalty_is_bounded():
    junk = [{"fact_id": f"nope.{i}", "quoted_value": i} for i in range(10)]
    v = run(junk, text="@a @b @c @d @e", conf=1.0)
    assert v.verification.penalty == 0.8
    assert v.confidence.final == 0.2 if v.verification.claims_verified >= 2 else v.confidence.final <= 0.5
