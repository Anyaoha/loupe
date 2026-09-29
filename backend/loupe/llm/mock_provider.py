"""Deterministic provider for tests, evals and keyless demos.

It reads the same facts/signals payload the real model gets and produces a schema-valid
insight that cites real fact ids with correct values. Behaviour knobs let the eval harness
simulate a misbehaving model (hallucinated numbers, unknown users, invented fact ids) so
the verifier's guardrails are exercised, not just assumed.
"""

import json
import re

from loupe.llm.base import LLMRequest, LLMResponse


class MockProvider:
    system = "mock"

    def __init__(
        self,
        model: str = "mock-insight-v1",
        *,
        hallucinate_values: bool = False,
        invent_facts: bool = False,
        unknown_actor: str | None = None,
        ungrounded_action: bool = False,
    ):
        self.model = model
        self.hallucinate_values = hallucinate_values
        self.invent_facts = invent_facts
        self.unknown_actor = unknown_actor
        self.ungrounded_action = ungrounded_action

    async def complete(self, req: LLMRequest) -> LLMResponse:
        payload = _extract_payload(req.user)
        facts: dict = payload.get("facts", {})
        signals: list = payload.get("signals", [])

        def val(fid: str):
            v = facts.get(fid, {}).get("value")
            if self.hallucinate_values and isinstance(v, (int, float)):
                return round(v * 1.37 + 3, 2)
            return v

        evidence = []
        if signals:
            top = signals[0]
            for fid in top.get("evidence_refs", [])[:3]:
                if fid in facts:
                    evidence.append({"fact_id": fid, "claim": f"{facts[fid]['label']} was {val(fid)}", "quoted_value": val(fid)})
            headline = top["title"]
            narrative = (
                f"{top['summary']} This is the strongest signal in the window and is the one worth acting on first. "
                f"The remaining signals are secondary and consistent with the same underlying pattern."
            )
            hypothesis = {"hypothesis": f"Most likely cause: the pattern behind '{top['kind']}' reflects a change in who is doing the work rather than a change in the work itself.", "model_confidence": 0.7}
        else:
            for fid in ("cur.totals.prs_merged", "cur.totals.commits", "cur.flow.time_to_merge_median"):
                if fid in facts:
                    evidence.append({"fact_id": fid, "claim": f"{facts[fid]['label']} was {val(fid)}", "quoted_value": val(fid)})
            headline = "Steady period, no drift detected"
            narrative = "Throughput, review load and merge times are within the range of the previous period. Nothing stands out as needing intervention."
            hypothesis = None

        covered = float((payload.get("coverage") or {}).get("covered_ratio", 1.0))
        if covered < 0.95:
            narrative += f" Note: synced data covers only {covered:.0%} of this window, so treat these figures as partial."

        if self.invent_facts:
            evidence.append({"fact_id": "cur.totals.deploys", "claim": "deploys were 42", "quoted_value": 42})
        if self.unknown_actor:
            narrative += f" @{self.unknown_actor} appears to be carrying most of the load."

        actions = [
            {
                "action": _ACTIONS.get(s["kind"], f"Review the '{s['title']}' signal with the team"),
                "rationale": s["summary"],
                "fact_ids": [f for f in s.get("evidence_refs", []) if f in facts][:3],
                "signal_id": s["id"],
            }
            for s in signals[:3]
            if any(f in facts for f in s.get("evidence_refs", []))
        ]
        if self.ungrounded_action:
            actions.insert(0, {"action": "Add a second deploy pipeline", "rationale": "deploys are failing", "fact_ids": ["cur.totals.deploys"], "signal_id": "sig.deploys"})

        body = {
            "headline": headline,
            "narrative": narrative,
            "root_cause": hypothesis,
            "confidence": 0.72 if signals else 0.6,
            "evidence": evidence,
            "recommended_actions": actions,
            "signals_considered": [s["id"] for s in signals],
        }
        text = json.dumps(body)
        return LLMResponse(text=text, model=self.model, input_tokens=len(req.user) // 4, output_tokens=len(text) // 4, finish_reason="end_turn")


_ACTIONS = {
    "review_concentration": "Pair a second reviewer on incoming PRs to spread review load",
    "time_to_merge_drift": "Look at the slowest open PRs and unblock or split them",
    "issue_turnaround_drift": "Triage the oldest open issues and assign owners",
    "velocity_change": "Check whether scope or staffing changed before reading the drop as a problem",
    "unreviewed_merges": "Require at least one approving review before merge",
    "stale_pr_backlog": "Close or rebase stale PRs in the next planning session",
    "commit_bus_factor": "Spread changes in the hot paths across more contributors",
    "committer_churn": "Check in with contributors who went quiet this period",
}


def _extract_payload(user_prompt: str) -> dict:
    m = re.search(r"<data>\s*(\{.*\})\s*</data>", user_prompt, re.S)
    return json.loads(m.group(1)) if m else {}
