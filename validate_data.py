#!/usr/bin/env python3
"""
validate_data.py — 顶层入口代理，委托执行 scripts/validate_data.py
"""
import runpy
import sys
from pathlib import Path

if __name__ == '__main__':
    target = Path(__file__).resolve().parent / 'scripts' / 'validate_data.py'
    sys.exit(runpy.run_path(str(target), run_name='__main__'))
