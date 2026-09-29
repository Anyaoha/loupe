# ADR 0002: Insight confidence is computed, not copied from the model

- Status: accepted
- Code: `backend/loupe/insights/verifier.py`, `backend/loupe/insights/calibration.py`

## Context

The insight is a model-written narrative about a repository's delivery data. Language models report confidence that does not track whether they are right, and a narrative can quote wrong numbers, cite data that does not exist, name people who are not in the repository, or recommend actions no data supports. A reader needs to know how much to trust each insight before acting on it.

## Decision

1. The model sees only a flat facts table (`{fact_id: {label, value, unit}}`) and pre-computed signals, and must cite fact ids for every claim and every recommended action.
2. The verifier checks each claim (id exists, quoted value matches within rounding and percent-vs-ratio tolerance), each action (cites at least one real fact id, and a real signal id if it names one), and every `@person` in the text against the people in the data.
3. Displayed confidence is computed:
   - `after_verification = model_confidence × (1 − penalty)`, where each failed claim or ungrounded action costs 0.15 and each unknown person 0.20, capped at 0.8 total
   - `coverage_cap = 0.5 + 0.5 × covered_ratio`
   - `final = min(after_verification, coverage_cap)`, and at most 0.5 if fewer than two claims verified
4. The whole breakdown is returned and shown. Failed claims and ungrounded actions stay visible, marked, rather than being removed.
5. Readers mark insights right or wrong. `/insights/calibration` compares the displayed confidence with those verdicts per prompt version (reliability buckets and a Brier score).

## Consequences

- A hallucinated number or an invented person lowers the displayed confidence, and the reader can see which claim caused it.
- Partial data can never produce a high-confidence insight.
- The penalties are hand-set, not learned. Calibration measures whether they are right, but does not yet correct them. Once there are a few dozen verdicts per band, fit an isotonic or Platt mapping per prompt version and show raw and calibrated confidence side by side.
- The verifier checks that citations are real, not that the reasoning is sound. A narrative can cite correct facts and still draw the wrong conclusion; human verdicts are the check for that.
- The eval harness runs adversarial mock models (hallucinated values, invented fact ids, unknown people, ungrounded actions) and fails if the verifier does not catch each one.

## Alternatives considered

- **Show the model's confidence as-is:** simplest, but not tied to anything checkable.
- **Drop failed claims silently:** looks cleaner and hides the model's error rate.
- **A second model as judge:** doubles cost and latency and adds another uncalibrated number. Deterministic checks against the facts table are cheaper and reproducible.
