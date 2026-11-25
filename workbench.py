#!/usr/bin/env python3
"""便于直接运行的命令行入口：python workbench.py report。"""
import sys
from pathlib import Path

# 兼容 `python -I /workspace/workbench.py`：隔离模式不会自动加入脚本目录。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hostdesk.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
