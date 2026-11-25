"""工作台状态、记录增量、时间账和查询接口。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .coverage import Relation, relation_between, resolve_need
from .models import HOST_ONLY, TRANSFER, Interpretation, Need, row_id
from .privacy import collect_protected_entities, scan_utterance, scope_for_question
from .semantics import infer_category, interpret_answer, interpret_question


class Workbench:
    def __init__(self, records: dict[str, list[dict[str, Any]]], state_path: str | Path | None = None):
        self.records = records
        self.state_path = Path(state_path) if state_path else None
        self.delta: dict[str, list[dict[str, Any]]] = {"questions": [], "drafts": [], "readback": []}
        if self.state_path and self.state_path.exists():
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            for table in self.delta:
                rows = saved.get(table, [])
                self.delta[table] = rows
                self.records.setdefault(table, []).extend(rows)
        self.protected = collect_protected_entities(self.records["questions"])

    @classmethod
    def load(cls, data_path: str | Path, state_path: str | Path | None = None) -> "Workbench":
        records = json.loads(Path(data_path).read_text(encoding="utf-8"))
        return cls(records, state_path)

    def save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.delta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ---------- 临时记录 ----------
    def add_question(
        self,
        text: str,
        project: str,
        sharing: str = "现场",
        category: str | None = None,
        received: int | None = None,
    ) -> dict[str, Any]:
        if category is None:
            interp = interpret_question(text)
            category = interp.needs[0].category if interp.needs else "未分类"
        row = {
            "id": row_id("Q", self.records["questions"]),
            "project": project,
            "received": received if received is not None else max(
                (q.get("received", 0) for q in self.records["questions"]), default=0
            )
            + 1,
            "category": category,
            "sharing": sharing,
            "text": text,
        }
        self.records["questions"].append(row)
        self.delta["questions"].append(row)
        # 新问句可能带来新的受保护实体。
        self.protected = collect_protected_entities(self.records["questions"])
        self.save_state()
        return row

    def add_draft(self, text: str, project: str, seconds: int, draft_id: str | None = None) -> dict[str, Any]:
        row = {
            "id": draft_id or row_id("D", self.records["drafts"]),
            "project": project,
            "seconds": int(seconds),
            "text": text,
        }
        self.records["drafts"].append(row)
        self.delta["drafts"].append(row)
        self.save_state()
        return row

    def add_readback(
        self,
        text: str,
        project: str,
        role: str,
        seconds: int,
        at: int | None = None,
        draft: str | None = None,
        readback_id: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": readback_id or row_id("R", self.records["readback"]),
            "at": at if at is not None else max((r.get("at", 0) for r in self.records["readback"]), default=0) + 1,
            "project": project,
            "role": role,
            "draft": draft,
            "seconds": int(seconds),
            "text": text,
        }
        # 保持按发生顺序排列。
        self.records["readback"].append(row)
        self.records["readback"].sort(key=lambda r: r["at"])
        self.delta["readback"].append(row)
        self.save_state()
        return row

    # ---------- 语义与事实 ----------
    def interpretation_for(self, table: str, row: dict[str, Any]) -> Interpretation:
        if table == "readback" and row.get("role") == "团队":
            return interpret_answer(row["text"])
        return interpret_question(row["text"])

    def project_facts(self, project_id: str) -> list[Any]:
        facts = []
        for row in self.records["readback"]:
            if row["project"] != project_id or row.get("role") != "团队":
                continue
            interp = interpret_answer(row["text"])
            for fact in interp.facts:
                # dataclass frozen; attach evidence id via a lightweight wrapper tuple.
                facts.append((row["id"], fact))
        return facts

    def pitch_facts(self, project_id: str) -> list[Any]:
        project = next(p for p in self.records["projects"] if p["id"] == project_id)
        interp = interpret_answer(project["pitch"])
        return [("pitch", f) for f in interp.facts]

    def question_view(self, qid: str) -> dict[str, Any]:
        q = next(q for q in self.records["questions"] if q["id"] == qid)
        interp = interpret_question(q["text"])
        scope = scope_for_question(q, interp)
        fact_pairs = self.project_facts(q["project"])
        facts = [f for _, f in fact_pairs]
        need_views = []
        for need in interp.needs:
            resolution = resolve_need(need, facts)
            evidence = [rid for rid, f in fact_pairs if f.evidence and f.evidence in resolution.reason]
            # Resolution reason usually does not contain evidence text; attach all relevant project evidence.
            if not evidence:
                evidence = [rid for rid, f in fact_pairs if f.topic == need.topic]
            need_views.append(
                {
                    **need.to_dict(),
                    "resolution": {**resolution.to_dict(), "evidence_ids": sorted(set(evidence))},
                }
            )

        statuses = [n["resolution"]["status"] for n in need_views]
        if not statuses:
            overall = "semantic_review_needed"
        elif all(s == "resolved" for s in statuses):
            overall = "answered"
        elif any(s in {"unresolved", "partial"} for s in statuses):
            overall = "open"
        else:
            overall = "blocked_on_future_or_premise"

        topics = {n.topic for n in interp.needs}
        pitch_context = [
            {"source": source, **fact.to_dict()}
            for source, fact in self.pitch_facts(q["project"])
            if fact.topic in topics or fact.category == q.get("category")
        ]

        return {
            **q,
            "scope": scope,
            "semantics": interp.to_dict(),
            "need_views": need_views,
            "pitch_context": pitch_context,
            "overall_status": overall,
        }

    def question_views(self) -> list[dict[str, Any]]:
        return [self.question_view(q["id"]) for q in self.records["questions"]]

    # ---------- 时间账 ----------
    def clock(self, window_id: str | None = None) -> dict[str, Any]:
        window = self.records["windows"][0] if window_id is None else next(
            w for w in self.records["windows"] if w["id"] == window_id
        )
        project_ids = set(window["projects"])
        used_total = 0
        category_used = {c: 0 for c in window["categories"]}
        segments = []
        for row in sorted(self.records["readback"], key=lambda r: r["at"]):
            if row["project"] not in project_ids:
                continue
            used_total += row["seconds"]
            category = infer_category(row["text"])
            basis = "text"
            if category not in window["categories"]:
                draft_id = row.get("draft")
                if draft_id:
                    draft = next((d for d in self.records["drafts"] if d["id"] == draft_id), None)
                    if draft:
                        category = infer_category(draft["text"])
                        basis = "draft-reference"
            if category not in window["categories"]:
                # 该项目若只有一个公布类别，可安全归入；否则标为未分类而不是乱扣。
                q_categories = {q["category"] for q in self.records["questions"] if q["project"] == row["project"]}
                if len(q_categories) == 1:
                    category = next(iter(q_categories))
                    basis = "project-single-category"
                else:
                    category = None
                    basis = "unclassified"
            if category:
                category_used[category] = category_used.get(category, 0) + row["seconds"]
            alerts = scan_utterance(row["text"], self.protected)
            segments.append(
                {
                    **row,
                    "inferred_category": category,
                    "category_basis": basis,
                    "privacy_alerts": alerts,
                }
            )

        category_remaining = {
            c: window["categories"][c] - category_used.get(c, 0) for c in window["categories"]
        }
        over_budget = []
        if window["total_seconds"] - used_total < 0:
            over_budget.append({"scope": "total", "seconds_over": used_total - window["total_seconds"]})
        for cat, rem in category_remaining.items():
            if rem < 0:
                over_budget.append({"scope": "category", "category": cat, "seconds_over": -rem})

        return {
            "window": window,
            "total_seconds": window["total_seconds"],
            "used_seconds": used_total,
            "remaining_seconds": window["total_seconds"] - used_total,
            "category_budget": dict(window["categories"]),
            "category_used": category_used,
            "category_remaining": category_remaining,
            "over_budget": over_budget,
            "segments": segments,
            "open_floors": self._open_floors(project_ids),
        }

    def _open_floors(self, project_ids: set[str]) -> list[dict[str, Any]]:
        """主持人已问、但同项目之后还没有团队回录时，提示先等答复。"""
        ordered = sorted(self.records["readback"], key=lambda r: r["at"])
        open_floors = []
        for row in ordered:
            if row["role"] != "主持人" or row["project"] not in project_ids:
                continue
            answered = any(
                r["project"] == row["project"] and r["role"] == "团队" and r["at"] > row["at"]
                for r in ordered
            )
            if not answered:
                open_floors.append({"readback_id": row["id"], "project": row["project"], "at": row["at"]})
        return open_floors

    def project_opportunities(self, window_id: str | None = None) -> list[dict[str, Any]]:
        clock = self.clock(window_id)
        project_ids = clock["window"]["projects"]
        result = []
        for pid in project_ids:
            rows = [r for r in self.records["readback"] if r["project"] == pid]
            host = [r for r in rows if r["role"] == "主持人"]
            team = [r for r in rows if r["role"] == "团队"]
            result.append(
                {
                    "project": pid,
                    "host_segments": len(host),
                    "team_segments": len(team),
                    "has_public_answer_opportunity": bool(team),
                    "first_chance_priority": len(team) == 0,
                }
            )
        return result

    # ---------- 草稿覆盖 ----------
    def draft_coverage(self, draft_id: str) -> dict[str, Any]:
        draft = next(d for d in self.records["drafts"] if d["id"] == draft_id)
        d_interp = interpret_question(draft["text"])
        category = infer_category(draft["text"]) or (d_interp.needs[0].category if d_interp.needs else None)
        blocked_entities = scan_utterance(draft["text"], self.protected)
        q_cover = []
        marginal_score = 0.0
        for q in self.records["questions"]:
            if q["project"] != draft["project"]:
                continue
            qv = self.question_view(q["id"])
            pairs = []
            q_full = True
            q_any = False
            q_marginal = 0.0
            for nview in qv["need_views"]:
                from .models import Need

                qneed = Need(
                    nview["key"],
                    nview["label"],
                    nview["category"],
                    nview["topic"],
                    tuple(nview["conditions"]),
                    nview.get("entity"),
                )
                best: Relation | None = None
                best_need_key = None
                for dneed in d_interp.needs:
                    rel = relation_between(qneed, dneed)
                    if best is None or (rel.level == "full" and best.level != "full"):
                        best = rel
                        best_need_key = dneed.key
                    elif rel.level == "partial" and best.level == "none":
                        best = rel
                        best_need_key = dneed.key
                if best is None:
                    best = Relation("none")
                weight = 0.0
                if best.level != "full":
                    q_full = False
                if best.level != "none":
                    q_any = True
                    status = nview["resolution"]["status"]
                    if status == "resolved":
                        weight = 0.0
                    elif status in {"premise_rejected", "entailed_negative"}:
                        weight = 0.1 if best.level == "partial" else 0.2
                    elif status == "partial":
                        weight = 0.55 if best.level == "full" else 0.25
                    else:
                        weight = 1.0 if best.level == "full" else 0.5
                    q_marginal += weight
                pairs.append(
                    {
                        "need_key": qneed.key,
                        "need_label": qneed.label,
                        "relation": best.to_dict(),
                        "candidate_need_key": best_need_key,
                        "resolution_status": nview["resolution"]["status"],
                        "weight": round(weight, 2),
                    }
                )
            if q_any:
                marginal_score += q_marginal
                q_cover.append(
                    {
                        "question_id": q["id"],
                        "sharing": q["sharing"],
                        "full_coverage": q_full,
                        "marginal_score": round(q_marginal, 2),
                        "pairs": pairs,
                    }
                )
        return {
            **draft,
            "category": category,
            "safe": not any(a["level"] == "unauthorized_public_naming" for a in blocked_entities),
            "privacy_alerts": blocked_entities,
            "semantics": d_interp.to_dict(),
            "audience": q_cover,
            "marginal_score": round(marginal_score, 2),
            "efficiency": round(marginal_score / draft["seconds"], 4) if draft["seconds"] else 0,
        }

    def all_draft_coverage(self) -> list[dict[str, Any]]:
        return [self.draft_coverage(d["id"]) for d in self.records["drafts"]]

    # ---------- 问句两两合用 ----------
    def pair_merge(self, a_id: str, b_id: str) -> dict[str, Any]:
        a = self.question_view(a_id)
        b = self.question_view(b_id)
        a_needs = [n for n in a["semantics"]["needs"]]
        b_needs = [n for n in b["semantics"]["needs"]]
        from .models import Need

        def to_need(n: dict[str, Any]) -> Need:
            return Need(n["key"], n["label"], n["category"], n["topic"], tuple(n["conditions"]), n.get("entity"))

        matches = []
        for an in a_needs:
            best = Relation("none")
            best_b = None
            for bn in b_needs:
                rel = relation_between(to_need(an), to_need(bn), include_bridges=True)
                if rel.level == "full" or (rel.level == "partial" and best.level == "none"):
                    best = rel
                    best_b = bn["key"]
            matches.append({"side": a_id, "need": an["label"], "key": an["key"], "other_key": best_b, **best.to_dict()})
        for bn in b_needs:
            best = Relation("none")
            best_a = None
            for an in a_needs:
                rel = relation_between(to_need(bn), to_need(an), include_bridges=True)
                if rel.level == "full" or (rel.level == "partial" and best.level == "none"):
                    best = rel
                    best_a = an["key"]
            matches.append({"side": b_id, "need": bn["label"], "key": bn["key"], "other_key": best_a, **best.to_dict()})

        full = [m for m in matches if m["level"] == "full"]
        partial = [m for m in matches if m["level"] == "partial"]
        only = [m for m in matches if m["level"] == "none"]
        # 注意：这里的 can_full_merge 表示两边语义点等价、可直接记为同一句代问。
        # 若只是部分重合，仍可在一个“复合问法/回答”中处理，但必须显式补入并保留
        # 所有独有条件，不能把它宣称为原来的同一句话。
        can_full_merge = bool(a_needs and b_needs) and all(m["level"] == "full" for m in matches)
        stage_compound_safe = bool(a["scope"]["stage_anonymized"] and b["scope"]["stage_anonymized"])
        required_clauses = []
        for view in (a, b):
            for n in view["need_views"]:
                required_clauses.append(
                    {
                        "question_id": view["id"],
                        "key": n["key"],
                        "label": n["label"],
                        "conditions": n["conditions"],
                    }
                )
        return {
            "questions": [a_id, b_id],
            "can_full_merge": can_full_merge,
            "equivalent_question_merge": can_full_merge,
            "safe_stage_compound_turn_possible": stage_compound_safe,
            "required_compound_clauses": required_clauses,
            "policy": "只有同一个公开回答能以同等限定解除两侧全部事实点，才允许记为完整合用；部分关系只共用对应争点。部分重合时可做复合问法，但必须带上全部独有条件并重新计时。",
            "full_matches": full,
            "partial_bridges": partial,
            "uncovered": only,
            "hypotheses": {
                a_id: a["semantics"]["hypotheses"],
                b_id: b["semantics"]["hypotheses"],
            },
        }

    def cluster_merge(self, qids: list[str], candidate_text: str | None = None) -> dict[str, Any]:
        """显式防止“甲乙部分重合、乙丙部分重合，于是三个一路合并”。"""
        views = [self.question_view(qid) for qid in qids]
        pairs = []
        for i, a in enumerate(qids):
            for b in qids[i + 1 :]:
                pairs.append(self.pair_merge(a, b))

        residual = []
        for view in views:
            for n in view["need_views"]:
                if n["resolution"]["status"] not in {"resolved"}:
                    residual.append(
                        {"question_id": view["id"], "key": n["key"], "label": n["label"], "conditions": n["conditions"]}
                    )

        single_draft_options = []
        for cov in self.all_draft_coverage():
            audience = {a["question_id"]: a for a in cov["audience"]}
            if not all(qid in audience for qid in qids):
                continue
            full = True
            for qid in qids:
                qv = next(v for v in views if v["id"] == qid)
                for n in qv["need_views"]:
                    pair = next((p for p in audience[qid]["pairs"] if p["need_key"] == n["key"]), None)
                    if not pair or pair["relation"]["level"] != "full":
                        full = False
            if full:
                single_draft_options.append({"draft_id": cov["id"], "seconds": cov["seconds"], "text": cov["text"]})

        result = {
            "questions": qids,
            "pairwise_reports": pairs,
            "transitive_shortcut_valid": False,
            "safe_stage_compound_possible": all(v["scope"]["stage_anonymized"] for v in views),
            "policy": "部分重合关系不能传递；只有一个包含全部独有条件的公开问法/回答，才允许把整组记为完整合用。复合问法必须重新登记读长。",
            "residual_needs": residual,
            "registered_single_wording_full_merge": single_draft_options,
            "can_use_one_registered_wording": bool(single_draft_options),
        }
        if candidate_text:
            interp = interpret_question(candidate_text)
            rows = []
            all_full = True
            for view in views:
                for n in view["need_views"]:
                    qneed = Need(n["key"], n["label"], n["category"], n["topic"], tuple(n["conditions"]), n.get("entity"))
                    best = Relation("none")
                    for cneed in interp.needs:
                        rel = relation_between(qneed, cneed)
                        if rel.level == "full":
                            best = rel
                            break
                        if rel.level == "partial" and best.level == "none":
                            best = rel
                    if best.level != "full":
                        all_full = False
                    rows.append({"question_id": view["id"], "need_key": n["key"], **best.to_dict()})
            result["candidate_text"] = candidate_text
            result["candidate_assessment"] = {"full_merge": all_full, "rows": rows}
        return result

    def answer_coverage(self, text: str, qids: list[str], *, include_recorded: bool = True) -> dict[str, Any]:
        """判断一段假设或实际回答是否足以同时解除若干问句。"""
        hypothetical = interpret_answer(text).facts
        question_rows = []
        sufficient_now = True
        all_followups_closed = True
        for qid in qids:
            q = next(q for q in self.records["questions"] if q["id"] == qid)
            facts = list(hypothetical)
            if include_recorded:
                facts = [f for _, f in self.project_facts(q["project"])] + facts
            qv = self.question_view(qid)
            rows = []
            for n in qv["need_views"]:
                need = Need(n["key"], n["label"], n["category"], n["topic"], tuple(n["conditions"]), n.get("entity"))
                resolution = resolve_need(need, facts)
                current_removed = resolution.status in {"resolved", "premise_rejected", "entailed_negative"}
                closed = resolution.status in {"resolved", "premise_rejected"} and not resolution.future_followup
                sufficient_now = sufficient_now and current_removed
                all_followups_closed = all_followups_closed and closed
                rows.append({"need_key": need.key, "label": need.label, **resolution.to_dict()})
            question_rows.append({"question_id": qid, "needs": rows})
        return {
            "text": text,
            "questions": qids,
            "same_answer_currently_sufficient": sufficient_now,
            "all_contentions_closed": all_followups_closed,
            "policy": "premise_rejected 表示预设被否定、条件问题消解；entailed_negative 只解除当下是非问，未来验证争点仍保留。",
            "results": question_rows,
        }

    # ---------- 会后/许可板 ----------
    def access_board(self) -> dict[str, Any]:
        permitted, permission_required, public_overflow = [], [], []
        for qv in self.question_views():
            item = {
                "question_id": qv["id"],
                "project": qv["project"],
                "category": qv["category"],
                "text": qv["text"],
                "sharing": qv["sharing"],
                "open_needs": [
                    {"key": n["key"], "label": n["label"], "status": n["resolution"]["status"]}
                    for n in qv["need_views"]
                    if n["resolution"]["status"] != "resolved"
                ],
            }
            if qv["sharing"] == TRANSFER or qv["scope"]["transfer_permitted"]:
                item["content_referral_allowed"] = True
                item["contact_submitter_allowed"] = True
                item["handoff_scope"] = "可交会后承办人；按原话只做会后单独联系，不得在台上点出其对象。"
                permitted.append(item)
            elif qv["sharing"] == HOST_ONLY:
                item["content_referral_allowed"] = False
                item["contact_submitter_allowed"] = False
                item["permission_gap"] = "提交人仅允许主持人查看且未许可转交；需先征得转交/会后联系许可。"
                permission_required.append(item)
            else:
                item["content_referral_allowed"] = True
                item["contact_submitter_allowed"] = False
                item["referral_scope"] = "只可把公开安全的问题内容放入会后公开答疑；现场/匿名授权不包含提交人联系许可。"
                public_overflow.append(item)
        return {
            "permitted_handoff": permitted,
            "permission_required": permission_required,
            "public_questions": public_overflow,
            "protected_entities": list(self.protected.values()),
        }
