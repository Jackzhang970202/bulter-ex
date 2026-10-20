# -*- coding: utf-8 -*-
"""dept-kb-agent 启动入口：python run.py"""
import asyncio
import os
import sys

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "vendor"))

from app.server import run  # noqa: E402

if __name__ == "__main__":
    print("dept-kb-agent 启动中 … 打开 http://127.0.0.1:8633")
    run()
