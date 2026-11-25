"""路演主持工作台。

常用入口：

    from hostdesk import Workbench, build_plan, report_data
"""
from .engine import Workbench
from .planner import build_plan
from .report import report_data, render_coordinator, render_host_report, render_stage_prompter

__all__ = [
    "Workbench",
    "build_plan",
    "report_data",
    "render_host_report",
    "render_stage_prompter",
    "render_coordinator",
]
