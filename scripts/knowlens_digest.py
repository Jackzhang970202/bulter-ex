# -*- coding: utf-8 -*-
"""SessionStart hook: print the current release digest of a space.

Written into a Claude Code project's .claude/settings.local.json by cc.inject.
Exits silently when the service is down so it never blocks a session.
"""
import argparse
import json
import os
import sys
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True)
    ap.add_argument("--port", default=os.environ.get("KNOWLENS_PORT", "8633"))
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/api/spaces/{args.space}/digest"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (data.get("current") or {}).get("content") or ""
        if content:
            print(content)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()