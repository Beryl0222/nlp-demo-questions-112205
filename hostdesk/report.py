"""汇总主持人、提词和会后承办人需要的中文视图。"""
from __future__ import annotations

from typing import Any

from .coverage import relation_between
from .models import Need
from .planner import build_plan
from .privacy import mask_text


def _need(d: dict[str, Any]) -> Need:
    return Need(d["key"], d["label"], d["category"], d["topic"], tuple(d["conditions"]), d.get("entity"))


DISPOSITION_LABELS = {
    "already_heard": "已由回录回答",
    "planned_stage_full": "已排问法可完整代问",
    "planned_stage_partial_residual_aftermeeting": "台上只部分代问，残留条件转会后",
    "neutral_generic_wording_can_silently_cover; source_and_object_remain_private": "中性公开问法可静默覆盖；来源和对象仍不公开",
    "neutral_generic_partial_silent_cover; residual_requires_postmeeting_or_permission": "中性问法仅能静默部分覆盖；残留点需会后处理或许可",
    "host_only_permission_required": "仅主持人，需先补转交/联系许可",
    "permitted_aftermeeting_handoff": "可按许可转会后",
    "future_session_or_aftermeeting": "依赖未来场次，需会后/后续场次跟进",
    "aftermeeting_or_register_timed_wording": "转会后，或先登记短问法读长",
}


def timeline_view(wb, window_id: str | None = None) -> list[dict[str, Any]]:
    clock = wb.clock(window_id)
    views = []
    for seg in clock["segments"]:
        item = {k: seg[k] for k in ("id", "at", "project", "role", "draft", "seconds", "text", "inferred_category", "privacy_alerts")}
        if seg["role"] == "主持人":
            interp = wb.interpretation_for("readback", seg)
            covers = []
            for q in wb.records["questions"]:
                if q["project"] != seg["project"]:
                    continue
                qv = wb.question_view(q["id"])
                for n in qv["semantics"]["needs"]:
                    best = None
                    for actual_need in interp.needs:
                        rel = relation_between(_need(n), actual_need)
                        if rel.level == "full":
                            best = rel
                            break
                        if rel.level == "partial" and best is None:
                            best = rel
                    if best and best.level != "none":
                        covers.append({"question_id": q["id"], "need": n["label"], **best.to_dict()})
            item["actual_question_coverage"] = covers
        if seg["role"] == "团队":
            interp = wb.interpretation_for("readback", seg)
            item["heard_facts"] = [f.to_dict() for f in interp.facts]
        views.append(item)
    return views


def merge_cases(wb) -> list[dict[str, Any]]:
    cases = []
    questions = wb.records["questions"]
    for i, a in enumerate(questions):
        for b in questions[i + 1 :]:
            if a["project"] != b["project"]:
                continue
            case = wb.pair_merge(a["id"], b["id"])
            interesting = case["can_full_merge"] or any(
                x["level"] != "none" for x in case["partial_bridges"] + case["uncovered"]
            )
            # 只展示至少有一条 full/partial 桥接的近义问题；完全无关不制造噪声。
            if case["full_matches"] or case["partial_bridges"]:
                cases.append(case)
    return cases


def report_data(wb, window_id: str | None = None) -> dict[str, Any]:
    plan = build_plan(wb, window_id)
    question_ids = {q["id"] for q in wb.records["questions"]}
    cluster_specs = [
        ["Q1", "Q2", "Q8"],
        ["Q5", "Q6", "Q9"],
    ]
    clusters = [wb.cluster_merge(ids) for ids in cluster_specs if all(qid in question_ids for qid in ids)]
    return {
        "projects": wb.records["projects"],
        "questions": wb.question_views(),
        "drafts": wb.all_draft_coverage(),
        "timeline": timeline_view(wb, window_id),
        "plan": plan,
        "access_board": wb.access_board(),
        "merge_cases": merge_cases(wb),
        "cluster_cases": clusters,
    }


def _line(char: str = "─", n: int = 72) -> str:
    return char * n


def render_host_report(data: dict[str, Any]) -> str:
    out: list[str] = []
    clock = data["plan"]["clock"]
    out.append("# 路演主持工作台（主持全量视图）")
    out.append("")
    out.append("## 1. 时间与类别额度")
    out.append(
        f"窗口 {clock['window']['id']}：总预算 {clock['total_seconds']}s，"
        f"已播出 {clock['used_seconds']}s，剩余 {clock['remaining_seconds']}s。"
    )
    for cat, budget in clock["category_budget"].items():
        out.append(
            f"- {cat}：预算 {budget}s，已用 {clock['category_used'].get(cat, 0)}s，"
            f"剩余 {clock['category_remaining'][cat]}s"
        )
    for over in clock.get("over_budget", []):
        if over["scope"] == "total":
            out.append(f"- ⚠️ 总时间已超 {over['seconds_over']}s；不能再排新问法，只能停止展开或转会后。")
        else:
            out.append(f"- ⚠️ {over['category']} 已超 {over['seconds_over']}s；不得占用其他类别额度。")
    for floor in clock.get("open_floors", []):
        out.append(f"- ⏳ {floor['project']} 的 {floor['readback_id']} 已发问但尚无后续团队回录；应先留答复窗口，不要叠问。")
    out.append("")

    out.append("## 2. 已播出记录：以实际 text 为准")
    for item in data["timeline"]:
        cat = item.get("inferred_category") or "未分类"
        out.append(f"### {item['id']} / 第{item['at']}段 / {item['role']} / {item['seconds']}s / {cat}")
        out.append(f"实际发言：{item['text']}")
        if item.get("draft"):
            out.append(f"草稿参考：{item['draft']}；草稿不是实际问出的整句。")
        for alert in item.get("privacy_alerts", []):
            out.append(f"⚠️ 公开性警报：{alert['message']}")
        for fact in item.get("heard_facts", []):
            out.append(f"- 听到事实：{fact['label']}＝{fact['value']}（{fact['polarity']}；依据：{fact['evidence']}）")
        for cov in item.get("actual_question_coverage", []):
            mark = "完整" if cov["level"] == "full" else "部分"
            miss = f"；漏掉：{'、'.join(cov['missing'])}" if cov.get("missing") else ""
            out.append(f"- 实际可记为{mark}代问 {cov['question_id']}：{cov['need']} — {cov['reason']}{miss}")
        out.append("")

    out.append("## 3. 问句预审与可代问听众")
    draft_index = {d["id"]: d for d in data["drafts"]}
    for q in data["questions"]:
        scope = q["scope"]
        stage = "可原话上台" if scope["stage_verbatim"] else ("可匿名/中性上台" if scope["stage_anonymized"] else "不可按原句上台")
        transfer = "可转交会后" if scope["transfer_permitted"] else "不可转交"
        out.append(f"### {q['id']} [{q['project']}/{q['category']}/{q['sharing']}] {stage}；{transfer}")
        out.append(f"原话：{q['text']}")
        for pf in q.get("pitch_context", []):
            out.append(f"- 投影稿背景（不等于台上新答复）：{pf['label']}＝{pf['value']}；{pf['evidence']}")
            if pf.get("key") == "privacy.offline_only":
                out.append("  讲稿含台下交流限制：台上优先问场景/状态，不逼问或复述未授权对象名；具体身份留到许可范围内的会后处理。")
        for h in q["semantics"]["hypotheses"]:
            out.append(f"- 预设/转述：{h['label']}")
        for n in q["need_views"]:
            r = n["resolution"]
            cond = f"（限定：{'、'.join(n['conditions'])}）" if n["conditions"] else ""
            out.append(f"- 事实点 [{r['status']}] {n['label']}{cond}：{r['reason']}")
        represented = []
        for d in data["drafts"]:
            aud = next((a for a in d["audience"] if a["question_id"] == q["id"]), None)
            if aud:
                full = all(p["relation"]["level"] == "full" for p in aud["pairs"])
                represented.append(f"{d['id']}={'全' if full else '部'}（边际分{aud['marginal_score']}）")
        if represented:
            out.append("- 候选问法覆盖：" + "；".join(represented))
        out.append("")

    out.append("## 4. 现在可执行的排期（只用已登记读长）")
    sharing_by_id = {q["id"]: q["sharing"] for q in data["questions"]}

    def audience_text(cand: dict[str, Any]) -> str:
        labels = []
        for a in cand.get("audience", []):
            qid = a["question_id"]
            if sharing_by_id.get(qid) in {"许可转交", "仅主持人"}:
                labels.append(f"{qid}（静默覆盖，不得提及其对象/来源）")
            else:
                labels.append(qid)
        return "、".join(labels)

    rp = data["plan"]["registered_plan"]
    if not rp["selected"]:
        out.append("- 当前没有正覆盖且放得进预算的草稿。")
    for i, cand in enumerate(rp["selected"], 1):
        out.append(f"{i}. {cand['id']} / {cand['category']} / {cand['seconds']}s：{cand['text']}")
        out.append(f"   可代问：{audience_text(cand) or '无'}")
    out.append(
        f"   排后 projected：总剩余 {rp['projected_remaining_seconds']}s，"
        f"类别剩余 {rp['projected_category_remaining']}"
    )
    for b in rp["blocked_first_chance"]:
        out.append(f"- 未补上的首次机会 {b['project']}：{b['reason']}")
    out.append("")

    out.append("## 5. 更短公开写法的优化方案（读长须由编辑登记后才生效）")
    ap = data["plan"]["advisory_short_wording_plan"]
    for cand in ap["selected"]:
        tag = "建议稿，未计时" if cand.get("provisional") else "已登记"
        silent = "；仅在主持视图显示暗中代问，不得公开提问人" if cand.get("silent_only") else ""
        out.append(f"- [{tag}] {cand['id']} / {cand['category']} / {cand['seconds']}s：{cand['text']}")
        out.append(f"  覆盖：{audience_text(cand)}{silent}")
    out.append("")

    out.append("## 6. 会后、转交许可与联系缺口")
    board = data["access_board"]
    out.append("### 经许可可转交/会后联系")
    for item in board["permitted_handoff"]:
        opens = "；".join(n["label"] + f"({n['status']})" for n in item["open_needs"])
        out.append(f"- {item['question_id']} [{item['sharing']}] {item['text']}")
        out.append(f"  范围：{item['handoff_scope']} 待跟进：{opens}")
    out.append("### 不可转交、仍缺联系许可")
    for item in board["permission_required"]:
        opens = "；".join(n["label"] + f"({n['status']})" for n in item["open_needs"])
        out.append(f"- {item['question_id']}：{item['permission_gap']} 待跟进：{opens}")
    out.append("### 公开/匿名问题的会后或残留争点")
    out.append("以下只把公开安全的问题内容转入会后公开答疑，不转交提交人身份或联系方式；“现场/匿名现场”不等于许可联系。")
    for d in data["plan"]["dispositions"]:
        if d["sharing"] in {"仅主持人", "许可转交"}:
            continue
        bits = []
        for n in d["needs"]:
            if n["disposition"].startswith("planned_stage_full") or n["heard_status"] == "resolved":
                continue
            label = DISPOSITION_LABELS.get(n["disposition"], n["disposition"])
            bits.append(f"{n['label']}→{label}")
        if bits:
            out.append(f"- {d['question_id']}：" + "；".join(bits))
    out.append("")

    out.append("## 7. 相近问法为何只合用一部分")
    for cluster in data.get("cluster_cases", []):
        out.append(f"### 传递性合并检查 {' + '.join(cluster['questions'])}")
        out.append("- 禁止因为相邻两两有重合就把整组当成同一个问题；部分重合不传递。")
        if cluster.get("safe_stage_compound_possible"):
            out.append("- 这组均允许现场/匿名处理：可造一个包含下列全部限定的复合问法，但不能按任何原句或 D1 整句记账，且必须重新计时。")
        else:
            out.append("- 组内含许可转交/仅主持人事项：不得为了台上合并而写入其对象或来源，只能用中性公开问法静默覆盖公共部分。")
        if cluster["can_use_one_registered_wording"]:
            for opt in cluster["registered_single_wording_full_merge"]:
                out.append(f"- 存在一个已登记完整问法 {opt['draft_id']}（{opt['seconds']}s）：{opt['text']}")
        else:
            out.append("- 当前没有任何一个已登记问法能以同等限定完整覆盖整组；各自未覆盖条件仍保留。")
        labels = "；".join(f"{x['question_id']}:{x['label']}" for x in cluster["residual_needs"][:8])
        if labels:
            out.append(f"- 残留事实点：{labels}")
    for case in data["merge_cases"]:
        out.append(f"### {' + '.join(case['questions'])}：等价合并={case['can_full_merge']}；可做安全复合轮={case.get('safe_stage_compound_turn_possible')}")
        if not case["can_full_merge"] and case.get("safe_stage_compound_turn_possible"):
            out.append("- 可在同一轮复合提问/回答中处理，但必须逐字保留下列独有条件，并按新问法读长计时。")
        for m in case["full_matches"]:
            out.append(f"- 可共用：[{m['side']}] {m['need']}（{m['reason']}）")
        for m in case["partial_bridges"]:
            miss = f"；仍缺 {'、'.join(m['missing'])}" if m.get("missing") else ""
            out.append(f"- 仅部分重合：[{m['side']}] {m['need']} — {m['reason']}{miss}")
        for m in case["uncovered"]:
            out.append(f"- 不能被另一边覆盖：[{m['side']}] {m['need']}")
    out.append("")
    return "\n".join(out)


def stage_data(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "registered": [
            {
                "id": c["id"],
                "category": c["category"],
                "seconds": c["seconds"],
                "text": c["text"],
            }
            for c in data["plan"]["registered_plan"]["selected"]
            if not c.get("privacy_alerts")
        ],
        "provisional_short_wordings": [
            {"id": c["id"], "category": c["category"], "seconds": c["seconds"], "text": c["text"]}
            for c in data["plan"]["advisory_short_wording_plan"]["selected"]
            if c.get("provisional")
        ],
        "rule": "只显示可上台句子；不显示提交人、仅主持人原话或受保护对象。",
    }


def _mask_value(value: Any, wb, role: str) -> Any:
    if isinstance(value, str):
        return mask_text(value, wb.protected, role)
    if isinstance(value, list):
        return [_mask_value(v, wb, role) for v in value]
    if isinstance(value, dict):
        return {k: _mask_value(v, wb, role) for k, v in value.items()}
    return value


def coordinator_data(data: dict[str, Any], wb) -> dict[str, Any]:
    safe = {
        "permitted_handoff": data["access_board"]["permitted_handoff"],
        "public_heard_timeline": data["timeline"],
        "scope_notice": "仅主持人且未许可转交的记录不包含在本视图；需先取得联系/转交许可。",
    }
    return _mask_value(safe, wb, "coordinator")


def render_stage_prompter(data: dict[str, Any]) -> str:
    """主持人提词视图：只给可以上台的句子，不展示仅主持人原话。"""
    out = ["# 台上提词（安全视图）", ""]
    plan = data["plan"]["registered_plan"]
    out.append("## 已登记读长、现在可念")
    for c in plan["selected"]:
        if c.get("privacy_alerts"):
            continue
        out.append(f"- [{c['category']}｜{c['seconds']}s] {c['text']}")
    out.append("")
    out.append("## 可考虑的更短中性写法（需编辑确认读长）")
    for c in data["plan"]["advisory_short_wording_plan"]["selected"]:
        if c.get("provisional"):
            out.append(f"- [建议 {c['seconds']}s] {c['text']}")
    out.append("")
    out.append("不得念出受保护客户/机构名，不得说明中性问法来自哪位提交人。")
    return "\n".join(out)


def render_coordinator(data: dict[str, Any], wb) -> str:
    """会后承办人：只见许可转交内容；仅主持人记录不出现。"""
    protected = wb.protected
    out = ["# 会后承办人视图", "", "## 可收件/转交"]
    for item in data["access_board"]["permitted_handoff"]:
        text = mask_text(item["text"], protected, "coordinator")
        out.append(f"- {item['question_id']} / {item['project']}：{text}")
        out.append(f"  范围：{item['handoff_scope']}")
        for n in item["open_needs"]:
            out.append(f"  - 待核实：{n['label']}（{n['status']}）")
    out.append("")
    out.append("## 已公开播出的回答（承办口径）")
    for seg in data["timeline"]:
        text = mask_text(seg["text"], protected, "coordinator")
        out.append(f"- {seg['id']}/{seg['role']}：{text}")
        for fact in seg.get("heard_facts", []):
            out.append(f"  - {fact['label']}：{fact['value']}")
    out.append("")
    out.append("仅主持人且未许可转交的内容不会出现在本视图；需由主持人先取得联系/转交许可。")
    return "\n".join(out)
