"""校验：只跑纯函数，不调大模型。"""

from .models import Budget, Constraint, FinalDraft, VerificationResult, Verdict


def check_budget(c: Budget, draft: FinalDraft) -> Verdict:
    """预算：cost_per_person 求和就是人均，乘人数是总价。"""
    if not draft.slots:
        return Verdict(constraint_id=c.id, ok=None, detail="还没有方案")

    per_person = 0
    for slot in draft.slots:
        if slot.cost_per_person is None:
            where = slot.place_name or slot.place_id or "某个活动"
            return Verdict(constraint_id=c.id, ok=None, detail=f"{where} 没有价格")
        per_person += slot.cost_per_person

    headcount = max(len(draft.participants), 1)
    total = per_person * headcount

    if c.per_person_max is not None and per_person > c.per_person_max:
        return Verdict(
            constraint_id=c.id,
            ok=False,
            detail=f"人均 {per_person} 超过上限 {c.per_person_max}（{headcount} 人）",
        )
    if c.total_max is not None and total > c.total_max:
        return Verdict(
            constraint_id=c.id,
            ok=False,
            detail=f"总价 {total} 超过上限 {c.total_max}",
        )
    return Verdict(
        constraint_id=c.id,
        ok=True,
        detail=f"人均 {per_person}，总价 {total}（{headcount} 人）",
    )


CHECKERS = {
    "budget": check_budget,
}


def verify(constraints: list[Constraint], draft: FinalDraft) -> VerificationResult:
    """只查硬约束。软约束不进这里。"""
    violations: list[Verdict] = []
    undecided: list[Verdict] = []

    for c in constraints:
        if c.severity != "hard":
            continue
        checker = CHECKERS.get(c.kind)
        if checker is None:
            continue
        verdict = checker(c, draft)
        if verdict.ok is False:
            violations.append(verdict)
        elif verdict.ok is None:
            undecided.append(verdict)

    return VerificationResult(
        passed=not violations,
        violations=violations,
        undecided=undecided,
    )
