"""按项目优先、类别额度和事实点覆盖安排剩余问法。"""
from __future__ import annotations

from typing import Any

from .coverage import relation_between
from .models import Need
from .semantics import interpret_question


# 这些只是“可考虑的短写法”和建议读长；未由编辑登记前，不能当成已确认预算。
PROPOSED_WORDINGS: dict[str, dict[str, Any]] = {
    "effect.cabinet_temperature": {
        "proposed_id": "SUG-CABINET-5C",
        "text": "棚外柜门在五度下能正常用吗？",
        "advisory_seconds": 8,
        "question_ids": ["Q4"],
    },
    "readability.two_line_line_of_sight": {
        "proposed_id": "SUG-READ-2LINES",
        "text": "两行字幕在视线内能读完吗？",
        "advisory_seconds": 8,
        "question_ids": ["Q6"],
        "silent_only": True,
    },
    "payment.exists": {
        "proposed_id": "SUG-PAYMENT-EXISTS",
        "text": "现在有没有非试用的已付费客户？",
        "advisory_seconds": 8,
        "question_ids": ["Q1", "Q3"],
    },
}


def _need_from_dict(n: dict[str, Any]) -> Need:
    return Need(n["key"], n["label"], n["category"], n["topic"], tuple(n["conditions"]), n.get("entity"))


def _candidate_view(wb, candidate: dict[str, Any]) -> dict[str, Any]:
    """把已登记草稿或建议短稿统一成候选。"""
    if "marginal_score" in candidate and "audience" in candidate:
        return candidate

    interp = interpret_question(candidate["text"])
    category = interp.needs[0].category if interp.needs else None
    audience = []
    score = 0.0
    for qid in candidate.get("question_ids", []):
        qv = wb.question_view(qid)
        qscore = 0.0
        pairs = []
        for nview in qv["need_views"]:
            qneed = _need_from_dict(nview)
            best = None
            for dneed in interp.needs:
                rel = relation_between(qneed, dneed)
                if best is None or (rel.level == "full" and best.level != "full"):
                    best = rel
            if best is None or best.level == "none":
                continue
            status = nview["resolution"]["status"]
            if status == "resolved":
                weight = 0.0
            elif status in {"premise_rejected", "entailed_negative"}:
                weight = 0.1
            elif status == "partial":
                weight = 0.5 if best.level == "full" else 0.2
            else:
                weight = 1.0 if best.level == "full" else 0.5
            qscore += weight
            pairs.append({"need_key": qneed.key, "relation": best.to_dict(), "weight": weight})
        if pairs:
            score += qscore
            audience.append({"question_id": qid, "marginal_score": round(qscore, 2), "pairs": pairs})
    return {
        "id": candidate["proposed_id"],
        "text": candidate["text"],
        "seconds": candidate["advisory_seconds"],
        "category": category,
        "safe": True,
        "audience": audience,
        "marginal_score": round(score, 2),
        "efficiency": round(score / candidate["advisory_seconds"], 4),
        "provisional": True,
        "silent_only": candidate.get("silent_only", False),
    }


def _select(wb, clock: dict[str, Any], include_proposed: bool) -> dict[str, Any]:
    remaining_total = clock["remaining_seconds"]
    remaining_cat = dict(clock["category_remaining"])
    opportunities = {o["project"]: o for o in wb.project_opportunities(clock["window"]["id"])}

    candidates = wb.all_draft_coverage()
    if include_proposed:
        existing_texts = {d["text"] for d in wb.records["drafts"]}
        for sug in PROPOSED_WORDINGS.values():
            if sug["text"] not in existing_texts:
                candidates.append(_candidate_view(wb, sug))

    candidates = [c for c in candidates if c.get("safe", True) and c.get("marginal_score", 0) > 0]
    selected: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    covered_full: set[tuple[str, str]] = set()

    def current_score(cov: dict[str, Any]) -> float:
        score = 0.0
        for aud in cov.get("audience", []):
            qid = aud["question_id"]
            for pair in aud.get("pairs", []):
                key = (qid, pair["need_key"])
                if key in covered_full:
                    continue
                rel = pair.get("relation", {})
                base = pair.get("weight", 0.0)
                if rel.get("level") == "partial":
                    base *= 0.7
                score += base
        return round(score, 3)

    def fits(cov: dict[str, Any]) -> bool:
        cat = cov.get("category")
        return (
            cov["seconds"] <= remaining_total
            and cat in remaining_cat
            and cov["seconds"] <= remaining_cat[cat]
        )

    def choose_for_project(project: str | None = None, category: str | None = None) -> dict[str, Any] | None:
        choices = []
        for cov in candidates:
            if cov["id"] in {s["id"] for s in selected}:
                continue
            if project is not None and cov.get("project") != project:
                # 建议候选没有 project 字段；通过 audience 找 project。
                qids = [a["question_id"] for a in cov.get("audience", [])]
                if not any(next(q for q in wb.records["questions"] if q["id"] == qid)["project"] == project for qid in qids):
                    continue
            if category is not None and cov.get("category") != category:
                continue
            score = current_score(cov)
            if score <= 0 or not fits(cov):
                continue
            choices.append((score / cov["seconds"], score, cov))
        if not choices:
            return None
        # 先比实际可覆盖的事实点/听众数，同分再看单位时间覆盖。
        choices.sort(key=lambda x: (x[1], x[0]), reverse=True)
        best = choices[0][2]
        best["_score_at_choice"] = choices[0][1]
        return best

    # 1) 每个项目第一次可公开答问机会优先。
    for project in clock["window"]["projects"]:
        if not opportunities[project]["first_chance_priority"]:
            continue
        cov = choose_for_project(project=project)
        if cov is None:
            blocked.append(
                {
                    "project": project,
                    "reason": "没有同时满足安全口径、正覆盖和剩余额度的已计时问法；需登记短稿或转会后。",
                }
            )
            continue
        selected.append(cov)
        remaining_total -= cov["seconds"]
        remaining_cat[cov["category"]] -= cov["seconds"]
        for aud in cov.get("audience", []):
            for pair in aud.get("pairs", []):
                if pair.get("relation", {}).get("level") == "full":
                    covered_full.add((aud["question_id"], pair["need_key"]))

    # 2) 之后按公布类别顺序和单位时间实际覆盖比较。
    for category in clock["window"]["categories"]:
        while True:
            cov = choose_for_project(category=category)
            if cov is None:
                break
            selected.append(cov)
            remaining_total -= cov["seconds"]
            remaining_cat[cov["category"]] -= cov["seconds"]
            for aud in cov.get("audience", []):
                for pair in aud.get("pairs", []):
                    if pair.get("relation", {}).get("level") == "full":
                        covered_full.add((aud["question_id"], pair["need_key"]))

    return {
        "provisional_wordings_included": include_proposed,
        "selected": [
            {
                "id": c["id"],
                "project": c.get("project"),
                "text": c["text"],
                "seconds": c["seconds"],
                "category": c["category"],
                "marginal_score_at_selection": c.get("_score_at_choice", current_score(c)),
                "provisional": c.get("provisional", False),
                "safe": c.get("safe", True),
                "privacy_alerts": c.get("privacy_alerts", []),
                "audience": c.get("audience", []),
            }
            for c in selected
        ],
        "blocked_first_chance": blocked,
        "projected_remaining_seconds": remaining_total,
        "projected_category_remaining": remaining_cat,
    }


def question_dispositions(wb, clock: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    selected = plan["selected"]
    result = []
    for qv in wb.question_views():
        selected_relations = []
        for cand in selected:
            for aud in cand.get("audience", []):
                if aud["question_id"] != qv["id"]:
                    continue
                for pair in aud.get("pairs", []):
                    selected_relations.append(
                        {
                            "candidate_id": cand["id"],
                            "candidate_text": cand["text"],
                            **pair,
                        }
                    )

        need_rows = []
        for nview in qv["need_views"]:
            key = nview["key"]
            rels = [r for r in selected_relations if r["need_key"] == key]
            best = None
            if any(r["relation"]["level"] == "full" for r in rels):
                best = next(r for r in rels if r["relation"]["level"] == "full")
            elif rels:
                best = rels[0]
            status = nview["resolution"]["status"]
            private = qv["sharing"] in {"许可转交", "仅主持人"}
            if status == "resolved":
                disposition = "already_heard"
            elif best and best["relation"]["level"] == "full" and private:
                disposition = "neutral_generic_wording_can_silently_cover; source_and_object_remain_private"
            elif best and best["relation"]["level"] == "full":
                disposition = "planned_stage_full"
            elif best and private:
                disposition = "neutral_generic_partial_silent_cover; residual_requires_postmeeting_or_permission"
            elif best:
                disposition = "planned_stage_partial_residual_aftermeeting"
            elif qv["sharing"] == "仅主持人":
                disposition = "host_only_permission_required"
            elif qv["sharing"] == "许可转交":
                disposition = "permitted_aftermeeting_handoff"
            elif nview["resolution"].get("future_followup"):
                disposition = "future_session_or_aftermeeting"
            else:
                disposition = "aftermeeting_or_register_timed_wording"
            need_rows.append(
                {
                    "key": key,
                    "label": nview["label"],
                    "heard_status": status,
                    "disposition": disposition,
                    "selected_relation": best,
                }
            )

        result.append(
            {
                "question_id": qv["id"],
                "project": qv["project"],
                "category": qv["category"],
                "sharing": qv["sharing"],
                "text": qv["text"],
                "scope": qv["scope"],
                "needs": need_rows,
            }
        )
    return result


def build_plan(wb, window_id: str | None = None) -> dict[str, Any]:
    clock = wb.clock(window_id)
    registered = _select(wb, clock, include_proposed=False)
    advisory = _select(wb, clock, include_proposed=True)
    return {
        "clock": clock,
        "opportunities": wb.project_opportunities(clock["window"]["id"]),
        "registered_plan": registered,
        "advisory_short_wording_plan": advisory,
        "dispositions": question_dispositions(wb, clock, registered),
        "proposed_wordings": list(PROPOSED_WORDINGS.values()),
    }
