"""校验：只跑纯函数，不调大模型。"""

from .models import (
    Activity,
    Availability,
    Budget,
    Candidate,
    Constraint,
    Diet,
    FinalDraft,
    VerificationResult,
    Verdict,
)

PRICE_KEY = "cost_per_person"


def check_budget(c: Budget, draft: FinalDraft, places: dict[str, Candidate]) -> Verdict:
    """预算：从 attrs 里取每人费用。取不到返回 None（无法判定）。"""
    if not draft.slots:
        return Verdict(constraint_id=c.id, ok=None, detail="还没有方案")

    per_person = 0
    for slot in draft.slots:
        price = slot.attrs.get(PRICE_KEY)
        if price is None:
            where = slot.place_name or slot.place_id or "某个活动"
            return Verdict(constraint_id=c.id, ok=None, detail=f"{where} 没有价格")
        per_person += price

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


def check_diet(c: Diet, draft: FinalDraft, places: dict[str, Candidate]) -> Verdict:
    """忌口：拿店里的 tags 和 avoid 求交集。查不到资料就返回 None。"""
    for slot in draft.slots:
        if slot.place_id is None:
            return Verdict(constraint_id=c.id, ok=None, detail="这个活动没有 place_id")
        place = places.get(slot.place_id)
        if place is None:
            return Verdict(
                constraint_id=c.id,
                ok=None,
                detail=f"缺 {slot.place_id} 的资料",
            )
        hit = set(c.avoid) & set(place.tags)
        if hit:
            return Verdict(
                constraint_id=c.id,
                ok=False,
                detail=f"{place.name} 命中 {sorted(hit)}",
            )
    return Verdict(constraint_id=c.id, ok=True)


def check_availability(c: Availability, draft: FinalDraft, places: dict[str, Candidate]) -> Verdict:
    """时间窗：所有安排都必须落在空闲区间内。和参与者无关时直接通过。"""
    named = set(c.who)
    if named and not (named & set(draft.participants)):
        return Verdict(constraint_id=c.id, ok=True, detail="这条约束和当前参与者无关")

    for slot in draft.slots:
        if slot.start_min < c.free_start_min or slot.end_min > c.free_end_min:
            where = slot.place_name or slot.activity
            return Verdict(
                constraint_id=c.id,
                ok=False,
                detail=f"{where} 超出 {c.free_start_min}-{c.free_end_min} 的空闲区间",
            )
    return Verdict(constraint_id=c.id, ok=True)


def check_activity(c: Activity, draft: FinalDraft, places: dict[str, Candidate]) -> Verdict:
    """方案里必须出现的活动，缺一个就打回。"""
    if not draft.slots:
        return Verdict(constraint_id=c.id, ok=None, detail="还没有方案")
    have = {s.activity for s in draft.slots}
    missing = [w for w in c.want if w not in have]
    if missing:
        return Verdict(
            constraint_id=c.id,
            ok=False,
            detail=f"方案里缺少 {missing}，现在只有 {sorted(have)}",
        )
    return Verdict(constraint_id=c.id, ok=True)


CHECKERS = {
    "budget": check_budget,
    "diet": check_diet,
    "activity": check_activity,
    "availability": check_availability,
}


def verify(
    constraints: list[Constraint],
    draft: FinalDraft,
    places: dict[str, Candidate] | None = None,
) -> VerificationResult:
    """只查硬约束。软约束不进这里。"""
    places = places or {}
    violations: list[Verdict] = []
    undecided: list[Verdict] = []

    for c in constraints:
        if c.severity != "hard":
            continue
        checker = CHECKERS.get(c.kind)
        if checker is None:
            continue
        verdict = checker(c, draft, places)
        if verdict.ok is False:
            violations.append(verdict)
        elif verdict.ok is None:
            undecided.append(verdict)

    return VerificationResult(
        passed=not violations,
        violations=violations,
        undecided=undecided,
    )
