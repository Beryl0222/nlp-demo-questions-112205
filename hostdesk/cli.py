"""python -m hostdesk 命令行入口。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine import Workbench
from .planner import build_plan
from .report import (
    coordinator_data,
    render_coordinator,
    render_host_report,
    render_stage_prompter,
    report_data,
    stage_data,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "records.json"
DEFAULT_STATE = Path.cwd() / ".hostdesk-state.json"


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hostdesk", description="路演主持工作台")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="records.json 路径")
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="增量状态 JSON 路径")
    sub = parser.add_subparsers(dest="command")

    p_report = sub.add_parser("report", help="输出完整中文工作台报告")
    p_report.add_argument("--json", action="store_true")
    p_report.add_argument("--window")
    p_preview = sub.add_parser("preview", help="预览问句、代问范围和台上提词")
    p_preview.add_argument("--role", choices=["host", "stage", "coordinator"], default="host")
    p_preview.add_argument("--json", action="store_true")
    p_preview.add_argument("--window")

    p_plan = sub.add_parser("plan", help="只输出排期 JSON/文本")
    p_plan.add_argument("--json", action="store_true")
    p_plan.add_argument("--window")

    p_merge = sub.add_parser("merge", help="解释两个问句的合用边界")
    p_merge.add_argument("question_a")
    p_merge.add_argument("question_b")
    p_merge.add_argument("--json", action="store_true")

    p_cluster = sub.add_parser("cluster", help="检查多个问句是否被传递性误合并")
    p_cluster.add_argument("question_ids", nargs="+")
    p_cluster.add_argument("--candidate-text")
    p_cluster.add_argument("--json", action="store_true")

    p_answer = sub.add_parser("answer-check", help="检查一段回答是否足以同时解除若干问句")
    p_answer.add_argument("text")
    p_answer.add_argument("question_ids", nargs="+")

    p_ask = sub.add_parser("ask", help="接住临时中文问句")
    p_ask.add_argument("text")
    p_ask.add_argument("--project", required=True)
    p_ask.add_argument("--sharing", default="现场", choices=["现场", "匿名现场", "许可转交", "仅主持人"])
    p_ask.add_argument("--category")
    p_ask.add_argument("--received", type=int)

    p_draft = sub.add_parser("draft", help="登记备选短问法和读长")
    p_draft.add_argument("text")
    p_draft.add_argument("--project", required=True)
    p_draft.add_argument("--seconds", required=True, type=int)

    p_record = sub.add_parser("record", help="记录一段实际主持/团队发言")
    p_record.add_argument("text")
    p_record.add_argument("--project", required=True)
    p_record.add_argument("--role", default="团队", choices=["主持人", "团队"])
    p_record.add_argument("--seconds", required=True, type=int)
    p_record.add_argument("--at", type=int)
    p_record.add_argument("--draft")

    sub.add_parser("board", help="公开/转交/联系许可板")
    sub.add_parser("timeline", help="实际回录与覆盖判定")

    p_serve = sub.add_parser("serve", help="启动本机网页工作台")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # 查询命令在没有状态文件时也应可读；变更命令会在 save 时创建。
    state_path = Path(args.state) if args.state else None
    wb = Workbench.load(args.data, state_path)

    if args.command in (None, "report"):
        data = report_data(wb, getattr(args, "window", None))
        if getattr(args, "json", False):
            _print_json(data)
        else:
            print(render_host_report(data))
        return 0

    if args.command == "preview":
        data = report_data(wb, getattr(args, "window", None))
        if args.json:
            if args.role == "stage":
                _print_json(stage_data(data))
            elif args.role == "coordinator":
                _print_json(coordinator_data(data, wb))
            else:
                _print_json(data)
        elif args.role == "stage":
            print(render_stage_prompter(data))
        elif args.role == "coordinator":
            print(render_coordinator(data, wb))
        else:
            print(render_host_report(data))
        return 0

    if args.command == "plan":
        plan = build_plan(wb, getattr(args, "window", None))
        if args.json:
            _print_json(plan)
        else:
            _print_json(
                {
                    "remaining_seconds": plan["clock"]["remaining_seconds"],
                    "category_remaining": plan["clock"]["category_remaining"],
                    "registered": [f"{x['id']} {x['seconds']}s {x['text']}" for x in plan["registered_plan"]["selected"]],
                    "advisory": [f"{x['id']} {x['seconds']}s {x['text']}" for x in plan["advisory_short_wording_plan"]["selected"]],
                }
            )
        return 0

    if args.command == "merge":
        case = wb.pair_merge(args.question_a, args.question_b)
        if args.json:
            _print_json(case)
        else:
            _print_json(case)
        return 0

    if args.command == "cluster":
        result = wb.cluster_merge(args.question_ids, args.candidate_text)
        _print_json(result)
        return 0

    if args.command == "answer-check":
        _print_json(wb.answer_coverage(args.text, args.question_ids))
        return 0

    if args.command == "ask":
        row = wb.add_question(args.text, args.project, args.sharing, args.category, args.received)
        _print_json(row)
        return 0

    if args.command == "draft":
        row = wb.add_draft(args.text, args.project, args.seconds)
        _print_json(row)
        return 0

    if args.command == "record":
        row = wb.add_readback(args.text, args.project, args.role, args.seconds, args.at, args.draft)
        _print_json(row)
        plan = build_plan(wb)
        clock = plan["clock"]
        print(
            f"[重排] 总剩余 {clock['remaining_seconds']}s；"
            f"类别剩余 {clock['category_remaining']}",
            file=sys.stderr,
        )
        for item in plan["registered_plan"]["selected"]:
            print(f"[下一问] {item['id']} {item['seconds']}s {item['text']}", file=sys.stderr)
        return 0

    if args.command == "board":
        _print_json(wb.access_board())
        return 0

    if args.command == "timeline":
        from .report import timeline_view

        _print_json(timeline_view(wb))
        return 0

    if args.command == "serve":
        from .web import serve

        serve(args.data, args.state, args.host, args.port)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
