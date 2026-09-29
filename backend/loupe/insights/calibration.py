"""Reliability of displayed confidence against human verdicts.

If Loupe says 0.7, roughly 70% of insights shown at ~0.7 should be confirmed. The report
buckets rated insights by the confidence they were shown with and compares that to the
observed hit rate, plus a Brier score as a single number to track across prompt versions.
"""

from collections.abc import Iterable

from loupe.schemas import CalibrationBucket, CalibrationReport

BUCKET_EDGES = (0.0, 0.4, 0.6, 0.8, 1.0)


def calibration_report(ratings: Iterable[tuple[float, bool]], prompt_version: str | None) -> CalibrationReport:
    """`ratings` are (confidence shown, confirmed?) pairs."""
    pairs = list(ratings)
    buckets = []
    for i, (lo, hi) in enumerate(zip(BUCKET_EDGES, BUCKET_EDGES[1:])):
        last = i == len(BUCKET_EDGES) - 2
        in_band = [(c, ok) for c, ok in pairs if lo <= c < hi or (last and c == hi)]
        confirmed = sum(ok for _, ok in in_band)
        buckets.append(CalibrationBucket(
            lower=lo,
            upper=hi,
            rated=len(in_band),
            confirmed=confirmed,
            hit_rate=round(confirmed / len(in_band), 3) if in_band else None,
            mean_confidence=round(sum(c for c, _ in in_band) / len(in_band), 3) if in_band else None,
        ))
    brier = round(sum((c - ok) ** 2 for c, ok in pairs) / len(pairs), 4) if pairs else None
    return CalibrationReport(
        prompt_version=prompt_version,
        rated=len(pairs),
        confirmed=sum(ok for _, ok in pairs),
        brier_score=brier,
        buckets=buckets,
    )
