#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：跑解析前先执行，缺什么一次说清。"""
import os
import shutil
import subprocess
import sys

OK, BAD = "[ok]", "[!!]"
rows, fatal = [], 0


def add(name, ok, detail=""):
    global fatal
    rows.append((OK if ok else BAD, name, detail))
    if not ok:
        fatal += 1


def which(cmd):
    return shutil.which(cmd)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    v = sys.version_info
    add("Python", v >= (3, 9), f"{v.major}.{v.minor}.{v.micro}")

    try:
        import requests  # noqa: F401
        add("requests", True, getattr(requests, "__version__", ""))
    except ImportError:
        add("requests", False, "pip install requests")

    node = which("node")
    add("Node", bool(node), node or "未找到，capture.mjs 需要 Node 22+")
    if node:
        try:
            out = subprocess.run([node, "--version"], capture_output=True,
                                 text=True, timeout=15).stdout.strip()
            add("Node 版本", out >= "v22", out)
        except Exception as e:
            add("Node 版本", False, str(e))

    chrome = os.environ.get("CHROME_PATH") or \
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    add("Chrome", os.path.exists(chrome), chrome)

    here = os.path.dirname(os.path.abspath(__file__))
    add("capture.mjs", os.path.exists(os.path.join(here, "capture.mjs")), "")

    w = max(len(r[1]) for r in rows) + 2
    for flag, name, detail in rows:
        print(f"{flag} {name.ljust(w)} {detail}")

    print()
    if fatal:
        print(f"❌ {fatal} 项不满足，先按上面提示补齐再运行。")
        return 1
    print("✅ 环境就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
