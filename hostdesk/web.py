"""仅使用标准库的本机工作台页面。

默认绑定 127.0.0.1，不对外网开放。页面是值班台的可选界面；
所有规则仍在 hostdesk 包和 CLI 中，便于自动化测试。
"""
from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .engine import Workbench
from .report import render_coordinator, render_host_report, render_stage_prompter, report_data


FORMS = """
<div class="card"><h3>接住临时问句</h3>
<form method="post" action="/ask">
<label>项目</label><select name="project"><option>P1</option><option>P2</option><option>P3</option></select>
<label>公开性</label><select name="sharing"><option>现场</option><option>匿名现场</option><option>许可转交</option><option>仅主持人</option></select>
<label>类别（可留空自动识别）</label><input name="category" placeholder="客户核实 / 实际效果 / 量产">
<label>中文问句</label><textarea name="text" required></textarea>
<button>提交问句</button></form></div>
<div class="card"><h3>登记短问法读长</h3>
<form method="post" action="/draft">
<label>项目</label><select name="project"><option>P1</option><option>P2</option><option>P3</option></select>
<label>读长（秒）</label><input name="seconds" type="number" min="1" required>
<label>问法</label><textarea name="text" required></textarea>
<button>登记草稿</button></form></div>
<div class="card"><h3>记录实际发言</h3>
<form method="post" action="/record">
<label>项目</label><select name="project"><option>P1</option><option>P2</option><option>P3</option></select>
<label>角色</label><select name="role"><option>团队</option><option>主持人</option></select>
<label>时长（秒）</label><input name="seconds" type="number" min="1" required>
<label>关联草稿 ID（可空）</label><input name="draft" placeholder="D1">
<label>实际 text（以这句为准）</label><textarea name="text" required></textarea>
<button>保存并重排</button></form></div>
"""


def make_handler(data_path: str | Path, state_path: str | Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # 本机工具保持终端安静；错误仍由异常响应体现。
            return

        def _wb(self) -> Workbench:
            return Workbench.load(data_path, state_path)

        def _send_report_page(self, report: str, forms: str = FORMS) -> None:
            safe_report = html.escape(report)
            html_body = f"{forms}<div class='card'><pre>{safe_report}</pre></div>"
            page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>路演主持工作台</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#17202a}}
header{{background:#13294b;color:#fff;padding:14px 22px}} nav a{{color:#cfe3ff;margin-right:16px;text-decoration:none}}
main{{max-width:1180px;margin:20px auto;padding:0 16px}} .grid{{display:grid;grid-template-columns:380px 1fr;gap:18px;align-items:start}}
.card{{background:#fff;border:1px solid #dce1e8;border-radius:10px;padding:16px;margin-bottom:16px}}
label{{display:block;font-size:13px;margin:8px 0 4px;color:#445}} input,select,textarea{{width:100%;box-sizing:border-box;padding:8px;border:1px solid #c4ccd8;border-radius:6px;font:inherit}}
textarea{{min-height:64px}} button{{margin-top:12px;background:#1769e0;color:#fff;border:0;border-radius:7px;padding:9px 14px;font-weight:600;cursor:pointer}}
pre{{white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#e5edf7;padding:16px;border-radius:10px;line-height:1.55;font-size:13px}}
@media(max-width:980px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><header><strong>路演主持工作台</strong><nav style="display:inline-block;margin-left:24px">
<a href="/">主持全量</a><a href="/stage">台上提词</a><a href="/coordinator">会后承办</a><a href="/report.json">JSON</a>
</nav></header><main><div class="grid"><section>{forms}</section><section><pre>{safe_report}</pre></section></div></main></body></html>""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def _send_text(self, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
            payload = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            wb = self._wb()
            if self.path == "/":
                self._send_report_page(render_host_report(report_data(wb)))
            elif self.path == "/stage":
                self._send_report_page(render_stage_prompter(report_data(wb)), forms="")
            elif self.path == "/coordinator":
                self._send_report_page(render_coordinator(report_data(wb), wb), forms="")
            elif self.path == "/report.json":
                import json

                self._send_text(json.dumps(report_data(wb), ensure_ascii=False, indent=2), "application/json; charset=utf-8")
            else:
                self._send_text("not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))

            def value(key: str, default: str = "") -> str:
                return form.get(key, [default])[0].strip()

            wb = self._wb()
            try:
                if self.path == "/ask":
                    category = value("category") or None
                    wb.add_question(value("text"), value("project"), value("sharing"), category)
                elif self.path == "/draft":
                    wb.add_draft(value("text"), value("project"), int(value("seconds")))
                elif self.path == "/record":
                    draft = value("draft") or None
                    wb.add_readback(value("text"), value("project"), value("role"), int(value("seconds")), draft=draft)
                else:
                    self.send_error(404)
                    return
            except (KeyError, ValueError) as exc:
                self._send_text(f"提交未保存：{exc}", "text/plain; charset=utf-8")
                return
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    return Handler


def serve(data_path: str | Path, state_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(data_path, state_path))
    print(f"主持工作台已启动：http://{host}:{port}（Ctrl+C 停止）")
    server.serve_forever()
