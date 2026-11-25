"""主持工作台的语义、合并、时间账和隐私流测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hostdesk import Workbench  # noqa: E402
from hostdesk.planner import build_plan  # noqa: E402


DATA = ROOT / "data" / "records.json"


class WorkbenchScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.wb = Workbench.load(DATA, self.state)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_question_facts_qualifiers_and_hypotheses_are_separated(self):
        q1 = self.wb.question_view("Q1")
        self.assertEqual(q1["need_views"][0]["key"], "payment.payer_identity")
        self.assertIn("排除仅试用客户", q1["need_views"][0]["conditions"])

        q6 = self.wb.question_view("Q6")
        self.assertEqual(q6["need_views"][0]["key"], "readability.two_line_line_of_sight")
        self.assertTrue(any(h["entity"] == "东津门诊" for h in q6["semantics"]["hypotheses"]))
        self.assertTrue(any(h["kind"] == "reported" for h in q6["semantics"]["hypotheses"]))

        q9 = self.wb.question_view("Q9")
        self.assertTrue(any(h["key"] == "presupposition.hospital_trial_done" for h in q9["semantics"]["hypotheses"]))
        self.assertTrue(any(h["kind"] == "warning" for h in q9["semantics"]["hypotheses"]))

    def test_clock_deducts_every_actual_segment_by_inferred_category(self):
        clock = self.wb.clock("W1")
        self.assertEqual(clock["used_seconds"], 24)
        self.assertEqual(clock["remaining_seconds"], 61)
        self.assertEqual(clock["category_remaining"]["客户核实"], 17)
        self.assertEqual(clock["category_remaining"]["实际效果"], 29)
        self.assertEqual(clock["category_remaining"]["量产"], 15)

    def test_actual_r1_is_not_backfilled_to_full_d1(self):
        timeline = {x["id"]: x for x in __import__("hostdesk.report", fromlist=["timeline_view"]).timeline_view(self.wb)}
        r1 = timeline["R1"]
        q1_cover = [x for x in r1["actual_question_coverage"] if x["question_id"] == "Q1"]
        self.assertTrue(q1_cover)
        self.assertEqual(q1_cover[0]["level"], "partial")
        self.assertIn("已付费限定", q1_cover[0]["missing"])

    def test_unsaid_payment_is_unknown_not_unpaid(self):
        q1 = self.wb.question_view("Q1")
        self.assertEqual(q1["need_views"][0]["resolution"]["status"], "unresolved")

        q2 = self.wb.question_view("Q2")
        statuses = {n["key"]: n["resolution"]["status"] for n in q2["need_views"]}
        self.assertEqual(statuses["trial.scene"], "resolved")
        self.assertEqual(statuses["trial.renewal_status"], "unresolved")

        q8 = self.wb.question_view("Q8")
        statuses = {n["key"]: n["resolution"]["status"] for n in q8["need_views"]}
        self.assertEqual(statuses["trial.renewed_scene_shop"], "partial")
        self.assertEqual(statuses["trial.renewal_payment_status"], "unresolved")

    def test_r3_rejects_hospital_premise_but_keeps_future_points(self):
        q9 = self.wb.question_view("Q9")
        lang = next(n for n in q9["need_views"] if n["key"] == "medical.hospital_confirmed_languages")
        self.assertEqual(lang["resolution"]["status"], "premise_rejected")
        self.assertTrue(lang["resolution"]["future_followup"])

        q5 = self.wb.question_view("Q5")
        patient = next(n for n in q5["need_views"] if n["key"] == "medical.patient_screen_confirmation")
        self.assertEqual(patient["resolution"]["status"], "entailed_negative")
        self.assertTrue(patient["resolution"]["future_followup"])

    def test_similar_questions_are_not_transitively_full_merged(self):
        q2_q8 = self.wb.pair_merge("Q2", "Q8")
        self.assertFalse(q2_q8["can_full_merge"])
        self.assertTrue(q2_q8["partial_bridges"])
        self.assertTrue(any(m.get("missing") for m in q2_q8["partial_bridges"]))

        q1_q2 = self.wb.pair_merge("Q1", "Q2")
        self.assertFalse(q1_q2["can_full_merge"])
        self.assertFalse(q1_q2["full_matches"])

        q1_q8 = self.wb.pair_merge("Q1", "Q8")
        self.assertFalse(q1_q8["can_full_merge"])
        self.assertTrue(q1_q8["partial_bridges"])

    def test_three_question_transitive_cluster_is_not_auto_merged(self):
        cluster = self.wb.cluster_merge(["Q1", "Q2", "Q8"])
        self.assertFalse(cluster["transitive_shortcut_valid"])
        self.assertFalse(cluster["can_use_one_registered_wording"])
        self.assertGreaterEqual(len(cluster["pairwise_reports"]), 3)
        self.assertTrue(any(x["question_id"] == "Q1" for x in cluster["residual_needs"]))
        self.assertTrue(any(x["question_id"] == "Q8" for x in cluster["residual_needs"]))

    def test_registered_plan_prioritizes_p3_then_fits_categories(self):
        plan = build_plan(self.wb, "W1")
        ids = [x["id"] for x in plan["registered_plan"]["selected"]]
        self.assertEqual(ids[0], "D4")
        self.assertIn("D1", ids)
        self.assertIn("D3", ids)
        self.assertNotIn("D2", ids)  # 场景已由 R2 基本答掉，且客户类别额度不容忍重复。
        self.assertNotIn("D5", ids)  # R3 已回答医院是否发生。
        self.assertEqual(plan["registered_plan"]["projected_category_remaining"]["客户核实"], 4)
        self.assertEqual(plan["registered_plan"]["projected_category_remaining"]["实际效果"], 9)

    def test_short_registered_wording_can_improve_coverage(self):
        self.wb.add_draft("棚外柜门在五度下能正常用吗？", "P1", 8, "D6")
        plan = build_plan(self.wb, "W1")
        ids = [x["id"] for x in plan["registered_plan"]["selected"]]
        self.assertIn("D6", ids)
        # D6(8s) 后实际效果类别仍有21s，D3(20s) 可继续覆盖读屏争点。
        self.assertIn("D3", ids)
        self.assertLessEqual(plan["registered_plan"]["projected_category_remaining"]["实际效果"], 1)

    def test_two_short_wordings_beat_one_long_partial_draft(self):
        self.wb.add_draft("棚外柜门在五度下能正常用吗？", "P1", 8, "D6")
        self.wb.add_draft("两行字幕在视线内能读完吗？", "P2", 8, "D7")
        plan = build_plan(self.wb, "W1")
        ids = [x["id"] for x in plan["registered_plan"]["selected"]]
        self.assertIn("D6", ids)
        self.assertIn("D7", ids)
        # 两个完整短问法共16s，优于20s且只部分覆盖的 D3；剩余13s 已放不下 D3。
        self.assertNotIn("D3", ids)

    def test_tight_budget_marks_unscheduleable_first_chance(self):
        window = self.wb.records["windows"][0]
        window["total_seconds"] = 50
        window["categories"] = {"客户核实": 20, "实际效果": 20, "量产": 10}
        plan = build_plan(self.wb, "W1")
        self.assertEqual(plan["registered_plan"]["selected"], [])
        self.assertTrue(plan["registered_plan"]["blocked_first_chance"])
        q7 = next(d for d in plan["dispositions"] if d["question_id"] == "Q7")
        self.assertTrue(all("aftermeeting" in n["disposition"] for n in q7["needs"]))

    def test_one_comprehensive_answer_can_clear_compound_pair(self):
        result = self.wb.answer_coverage(
            "没有非试用的已付费客户；当前试用在商铺，没有续订，也没有续订付款。",
            ["Q1", "Q2", "Q8"],
        )
        self.assertTrue(result["same_answer_currently_sufficient"])
        self.assertTrue(result["all_contentions_closed"])

    def test_order_is_not_equated_with_payment(self):
        interp = self.wb.interpretation_for(
            "question",
            {"text": "现在有订单吗？订单先不问，五度柜门能用吗？"},
        )
        keys = {n.key for n in interp.needs}
        self.assertIn("order.exists", keys)
        self.assertIn("effect.cabinet_temperature", keys)

        q4 = self.wb.question_view("Q4")
        self.assertEqual([n["key"] for n in q4["need_views"]], ["effect.cabinet_temperature"])

    def test_privacy_board_separates_transfer_and_missing_permission(self):
        board = self.wb.access_board()
        permitted_ids = {x["question_id"] for x in board["permitted_handoff"]}
        permission_ids = {x["question_id"] for x in board["permission_required"]}
        self.assertEqual(permitted_ids, {"Q3"})
        self.assertEqual(permission_ids, {"Q6"})

    def test_unauthorized_naming_is_flagged_and_replan_uses_new_seconds(self):
        row = self.wb.add_readback("红渠美术馆其实还在试用，没有付款。", "P1", "团队", 11)
        clock = self.wb.clock("W1")
        segment = next(s for s in clock["segments"] if s["id"] == row["id"])
        self.assertTrue(any(a["level"] == "unauthorized_public_naming" for a in segment["privacy_alerts"]))
        self.assertEqual(clock["used_seconds"], 35)
        self.assertEqual(clock["category_remaining"]["客户核实"], 6)

    def test_new_question_is_persisted_to_state(self):
        row = self.wb.add_question(
            "零下二度时样件柜门还能开合吗？", "P1", "现场", "实际效果"
        )
        self.assertTrue(row["id"].startswith("Q"))
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["questions"][0]["id"], row["id"])

        reloaded = Workbench.load(DATA, self.state)
        self.assertTrue(any(q["id"] == row["id"] for q in reloaded.records["questions"]))


if __name__ == "__main__":
    unittest.main()
