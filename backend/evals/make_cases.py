"""Regenerates the golden SignalsReport cases from the test fixtures.

Run after changing the metrics engine or signal detectors:  python -m evals.make_cases
The cases are committed so the eval harness runs without a database or network.
"""

import json
from pathlib import Path

from loupe import db as dbmod
from loupe.signals import compute_signals

CASES = Path(__file__).parent / "cases"


def main() -> None:
    import tempfile

    from tests import conftest, test_signals

    with tempfile.TemporaryDirectory() as tmp:
        dbmod.init_engine(f"sqlite:///{tmp}/cases.db")
        with dbmod.session_scope() as s:
            quiet = conftest.seed_acme_widgets(s)
            drifting = test_signals.drifting_repo.__wrapped__(s)
            _write("quiet_repo", compute_signals(s, quiet, conftest.WINDOW_START, conftest.WINDOW_END), {
                "expect_top_signal": None, "confidence": [0.3, 0.75], "root_cause_allowed": False,
            })
            _write("drifting_repo", compute_signals(s, drifting, test_signals.START, test_signals.END), {
                "expect_top_signal": "velocity_change", "confidence": [0.5, 0.95], "root_cause_allowed": True,
            })
            # Same drifting data, but a window that reaches beyond what was synced -> partial coverage.
            far_end = test_signals.END.replace(month=7)
            _write("partial_coverage", compute_signals(s, drifting, test_signals.START, far_end), {
                "expect_top_signal": None, "confidence": [0.0, 0.85], "root_cause_allowed": True, "must_mention_coverage": True,
            })
    print(f"wrote cases to {CASES}")


def _write(name: str, report, expectations: dict) -> None:
    CASES.mkdir(exist_ok=True)
    payload = {"name": name, "expectations": expectations, "report": report.model_dump(mode="json")}
    (CASES / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
