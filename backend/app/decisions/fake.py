from app.decisions.contract import (
    ReasonCode,
    TriageAction,
    TriageDecision,
    TriageDecisionBatch,
    TriageItem,
    align_decisions,
)


ScriptedDecision = tuple[TriageAction, ReasonCode, float]


class FakeDecisionProvider:
    name = "fake"

    def __init__(
        self,
        scripted: dict[str, ScriptedDecision | None] | None = None,
        *,
        default: ScriptedDecision | None = ("mark_read", ReasonCode.OTHER, 0.6),
        error: Exception | None = None,
    ):
        self.scripted = dict(scripted or {})
        self.default = default
        self.error = error
        self.calls: list[list[str]] = []

    def decide_triage(self, items: list[TriageItem]) -> TriageDecisionBatch:
        self.calls.append([item.item_id for item in items])
        if self.error is not None:
            raise self.error
        decisions = [
            self._decision(item.item_id, self.scripted.get(item.item_id, self.default))
            for item in items
        ]
        return TriageDecisionBatch(
            provider=self.name,
            model="fake",
            decisions=align_decisions(items, decisions),
            requests=1,
        )

    @staticmethod
    def _decision(item_id: str, scripted: ScriptedDecision | None) -> TriageDecision:
        if scripted is None:
            return TriageDecision(item_id=item_id, status="abstained")
        action, reason_code, confidence = scripted
        return TriageDecision(
            item_id=item_id,
            status="ok",
            action=action,
            reason_code=reason_code,
            confidence=confidence,
            probabilities={action: confidence},
        )
